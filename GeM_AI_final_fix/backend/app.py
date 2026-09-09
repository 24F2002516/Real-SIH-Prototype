import os
import json
import shutil
import tempfile
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename

from models import db, Tender, Requirement, Bid, BidDocument, AIAnalysis, ReviewAction
from extract import extract_text
from compliance_engine import evaluate_bid
from report_genrator import generate_tender_report, generate_bid_report

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLED_DB = os.path.join(BASE_DIR, "compliance.db")
UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "sih_bid_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
CORS(app)

# Vercel's deployed filesystem is read-only. Use DATABASE_URL for real persistence;
# otherwise keep a disposable SQLite DB in /tmp so the prototype still runs.
database_url = os.environ.get("DATABASE_URL")
if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    runtime_db = os.path.join(tempfile.gettempdir(), "compliance.db")
    if not os.path.exists(runtime_db) and os.path.exists(BUNDLED_DB):
        try:
            shutil.copy2(BUNDLED_DB, runtime_db)
        except OSError:
            pass
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{runtime_db}"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

ALLOWED_EXTENSIONS = {"pdf", "docx", "txt", "csv"}

db.init_app(app)


def ensure_database():
    with app.app_context():
        db.create_all()
        # A clean deployment gets a useful demo tender without requiring a local seed command.
        if Tender.query.count() == 0:
            tender = Tender(
                title="Supply of Industrial Valves for Refinery Maintenance",
                gem_bid_number="GEM/2026/B/000123",
                department="Ministry of Petroleum & Natural Gas",
                description="Sample tender for demonstrating automated bid compliance verification.",
            )
            db.session.add(tender)
            db.session.flush()
            db.session.add_all([
                Requirement(tender_id=tender.id, name="GST Registration Certificate", rule_type="DOCUMENT_PRESENT", match_value="gst", weight=2, is_mandatory=True),
                Requirement(tender_id=tender.id, name="EMD Payment Receipt", rule_type="DOCUMENT_PRESENT", match_value="emd", weight=2, is_mandatory=True),
                Requirement(tender_id=tender.id, name="Relevant valve experience", rule_type="KEYWORD_MATCH", match_value="valve", weight=1.5, is_mandatory=False),
                Requirement(tender_id=tender.id, name="Minimum Annual Turnover (Rs. 50 Lakh)", rule_type="MIN_VALUE", field_name="annual_turnover", numeric_bound=5000000, weight=2, is_mandatory=True),
                Requirement(tender_id=tender.id, name="Minimum 3 Years Experience", rule_type="MIN_VALUE", field_name="experience_years", numeric_bound=3, weight=1.5, is_mandatory=True),
            ])
            db.session.commit()


ensure_database()


# Serve the prototype frontend from the same Flask deployment.
# Vercel's Flask preset sends requests to this application, so the root URL
# must explicitly return the bundled index.html.
FRONTEND_FILE = os.path.join(os.path.dirname(BASE_DIR), "index.html")


@app.get("/")
def frontend():
    return send_from_directory(os.path.dirname(FRONTEND_FILE), os.path.basename(FRONTEND_FILE))


@app.get("/favicon.ico")
def favicon():
    return ("", 204)


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "GeM Bid Compliance Verification API"})



# Real AI chat endpoint -----------------------------------------------------
def _groq_chat(question, system_prompt, conversation, json_mode=False):
    """Call Groq Chat Completions server-side. The API key never reaches the browser."""
    import urllib.request
    import urllib.error

    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        raise RuntimeError("AI is not configured. Add GROQ_API_KEY in Vercel Environment Variables.")

    base_url = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    model = os.environ.get("GROQ_MODEL", os.environ.get("AI_MODEL", "openai/gpt-oss-120b"))

    messages = [{"role": "system", "content": system_prompt}]
    for item in conversation[-10:]:
        role = "assistant" if item.get("role") == "assistant" else "user"
        text = str(item.get("text", "")).strip()
        if text:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": question})

    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1400 if json_mode else 900,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {groq_key}",
            "Content-Type": "application/json",
            # Groq is fronted by Cloudflare; urllib's default Python-urllib
            # user-agent can be rejected with HTTP 403 / error code 1010.
            "User-Agent": "GeM-Compliance-AI/1.0",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        app.logger.error("Groq provider HTTP %s: %s", exc.code, detail[:1500])
        try:
            err_json = json.loads(detail)
            err = err_json.get("error") if isinstance(err_json, dict) else None
            if isinstance(err, dict):
                msg = err.get("message") or str(err)
                typ = err.get("type")
                code = err.get("code")
                suffix = f" [{typ}]" if typ else ""
                if code:
                    suffix += f" code={code}"
                raise RuntimeError(f"Groq provider error ({exc.code}){suffix}: {msg[:700]}")
        except (ValueError, TypeError):
            pass
        raise RuntimeError(f"Groq provider error ({exc.code}): {detail[:700]}")
    except urllib.error.URLError as exc:
        app.logger.error("Groq provider connection error: %s", exc.reason)
        raise RuntimeError(f"Could not reach Groq AI provider: {exc.reason}")

    try:
        answer = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        app.logger.error("Unexpected Groq response: %s", str(data)[:1500])
        raise RuntimeError("Groq returned an unexpected response.")
    if not answer:
        raise RuntimeError("Groq returned an empty response.")
    return answer


def _ai_context(tender, bid):
    requirements = [r.to_dict() for r in tender.requirements]
    bid_data = bid.to_dict(detailed=True) if bid else None
    evidence = []
    if bid:
        for doc in bid.documents:
            text = (doc.extracted_text or "").strip()
            evidence.append({"filename": doc.filename, "extracted_text": text[:7000]})
    return {
        "tender": tender.to_dict(),
        "requirements": requirements,
        "bid": bid_data,
        "evidence": evidence,
    }



def _groq_json(prompt, system_prompt):
    """Call Groq and parse a JSON object. Used for structured AI intelligence."""
    raw = _groq_chat(prompt, system_prompt, [], json_mode=True)
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"): text = text[:-3]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end+1])
        raise RuntimeError("AI returned non-JSON structured analysis.")


def _normalize_deep_analysis(result, bid):
    """Guarantee the UI receives actionable eligibility/recommendation fields even if the LLM omits them."""
    if not isinstance(result, dict):
        result = {}
    results = list(getattr(bid, "results", []) or [])
    status = str(getattr(bid, "status", "NEEDS_REVIEW") or "NEEDS_REVIEW").upper()
    failed = [r for r in results if not bool(r.passed)]
    mandatory_failed = [r for r in failed if bool(r.is_mandatory)]

    # Normalize eligibility_assessment into the object expected by the UI.
    ea = result.get("eligibility_assessment")
    if not isinstance(ea, dict):
        ea = {}
    ea_status = str(ea.get("status") or "").strip()
    if not ea_status:
        ea_status = {
            "COMPLIANT": "ELIGIBLE",
            "NON_COMPLIANT": "NOT ELIGIBLE",
            "NEEDS_REVIEW": "REQUIRES HUMAN REVIEW",
        }.get(status, "REQUIRES HUMAN REVIEW")
    reasons = ea.get("reasons")
    if not isinstance(reasons, list) or not reasons:
        if mandatory_failed:
            reasons = [f"Mandatory requirement not satisfied: {r.requirement_name}" for r in mandatory_failed]
        elif status == "COMPLIANT":
            reasons = ["All mandatory requirements are currently satisfied by the system validation."]
        else:
            reasons = ["The bid requires human review before a final procurement decision."]
    ea["status"] = ea_status
    ea["reasons"] = reasons
    result["eligibility_assessment"] = ea

    # Normalize recommendation. This is a system-generated fallback, not an LLM claim.
    recommendation = result.get("recommendation")
    recommendation_from_llm = isinstance(recommendation, str) and bool(recommendation.strip())
    if not recommendation_from_llm:
        if status == "COMPLIANT":
            recommendation = "Proceed to human verification. All mandatory requirements are currently satisfied; verify document authenticity and final procurement conditions before approval."
        elif status == "NON_COMPLIANT":
            names = ", ".join(r.requirement_name for r in mandatory_failed[:3])
            recommendation = f"Do not proceed until the mandatory compliance issues are resolved: {names}. Request clarification or corrected evidence where procurement policy permits."
        else:
            recommendation = "Send the bid for human review and investigate missing, ambiguous or conflicting evidence before taking final procurement action."
    result["recommendation"] = recommendation

    # If the model omitted confidence, calculate a transparent evidence-based fallback.
    try:
        conf = float(result.get("confidence"))
    except (TypeError, ValueError):
        conf = None
    if conf is None or not 0 <= conf <= 100:
        total = len(results)
        passed = sum(1 for r in results if bool(r.passed))
        if total:
            base = 90.0 + (10.0 * passed / total) if passed == total else 70.0 + (20.0 * passed / total)
            conf = round(min(base, 99.0), 1)
        else:
            conf = 60.0
    result["confidence"] = conf
    result.setdefault("risks", [])
    result.setdefault("inconsistencies", [])
    result.setdefault("missing_documents", [])
    result.setdefault("clause_analysis", [])
    result.setdefault("evidence_matches", [])
    result["eligibility_assessment"]["system_status"] = status
    result["recommendation_source"] = "LLM" if recommendation_from_llm else "AI + system validation fallback"
    return result


def _deep_ai_analysis(tender, bid):
    context = _ai_context(tender, bid)
    system = """You are the GeM AI Compliance Intelligence Engine. Produce evidence-grounded structured analysis of a procurement bid. Use ONLY the supplied tender, requirements, vendor declarations, compliance results and document text. Do not invent evidence. You may infer semantic relationships, but mark uncertainty. Return valid JSON only with keys: executive_summary, clause_analysis, evidence_matches, risks, inconsistencies, missing_documents, eligibility_assessment, confidence, recommendation. NEVER omit eligibility_assessment, confidence, or recommendation. eligibility_assessment must be an object with status and reasons. clause_analysis is an array of objects with requirement, interpretation, mandatory, extracted_constraints, evidence, reasoning, status, confidence. evidence_matches is an array with requirement, document, evidence_quote_or_summary, match_type (semantic/exact/declared/missing), reasoning, confidence. risks and inconsistencies are arrays of objects with severity, issue, evidence, action. missing_documents is an array. confidence is 0-100. recommendation is a concise human-review recommendation."""
    prompt = "Analyze this tender and bid deeply. Perform clause decomposition, semantic evidence matching, document classification, missing-evidence detection, inconsistency detection, risk analysis, and explainable eligibility assessment. Exact numeric thresholds must be respected from the supplied rule results; do not replace deterministic arithmetic with guesswork.\n\nCONTEXT:\n" + json.dumps(context, ensure_ascii=False, default=str)
    result = _groq_json(prompt, system)
    result = _normalize_deep_analysis(result, bid)
    result["engine"] = "Groq AI + deterministic validation"
    return result

@app.post("/api/ai/chat")
def ai_chat():
    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    tender_id = data.get("tender_id")
    bid_id = data.get("bid_id")
    language = str(data.get("language", "en"))
    history = data.get("history") if isinstance(data.get("history"), list) else []

    tender = db.session.get(Tender, tender_id) if tender_id else None
    bid = db.session.get(Bid, bid_id) if bid_id else None
    if bid and not tender:
        tender = db.session.get(Tender, bid.tender_id)
    if tender_id and not tender:
        return jsonify({"error": "Tender not found"}), 404
    if bid_id and not bid:
        return jsonify({"error": "Bid not found"}), 404

    context = _ai_context(tender, bid) if tender else {"tender": None, "requirements": [], "bid": None, "evidence": []}
    lang_name = {"en": "English", "hi": "Hindi", "mr": "Marathi", "ta": "Tamil", "te": "Telugu"}.get(language, "English")
    system_prompt = f"""You are GeM Compliance Copilot, an AI procurement decision-support assistant.
Answer the user's question using the tender, bid, compliance results, and extracted evidence supplied below. The selected response language is {lang_name}; answer in that language unless the user clearly asks for another language.

Rules:
- Be precise and evidence-based. Never invent a requirement, document, value, or decision.
- Distinguish tender requirements from vendor declarations and extracted evidence.
- If evidence is missing or ambiguous, say so and recommend human review.
- Explain failures by comparing required value/condition with the vendor evidence when available.
- Treat the compliance engine result as the current system result, not as an unquestionable legal/procurement decision.
- For procurement decisions, recommend human verification before final action.
- Keep answers concise but useful, with bullets when helpful.

CURRENT CONTEXT:
{json.dumps(context, ensure_ascii=False, default=str)}"""

    try:
        answer = _groq_chat(question, system_prompt, history)
        return jsonify({"answer": answer, "model": os.environ.get("GROQ_MODEL", os.environ.get("AI_MODEL", "openai/gpt-oss-120b")), "provider": "Groq", "real_ai": True})
    except RuntimeError as exc:
        app.logger.error("AI chat configuration/provider error: %s", exc)
        return jsonify({"error": str(exc), "real_ai": False}), 503
    except Exception as exc:
        app.logger.exception("Unhandled AI chat error")
        return jsonify({"error": f"AI request failed: {exc}", "real_ai": False}), 500

@app.post("/api/ai/analyze/<int:bid_id>")
def ai_analyze_bid(bid_id):
    bid = db.session.get(Bid, bid_id)
    if not bid:
        return jsonify({"error": "Bid not found"}), 404
    tender = db.session.get(Tender, bid.tender_id)
    try:
        analysis = _deep_ai_analysis(tender, bid)
        row = AIAnalysis(bid_id=bid.id, payload=analysis)
        db.session.add(row)
        db.session.commit()
        return jsonify(row.to_dict())
    except Exception as exc:
        app.logger.exception("Deep AI analysis failed")
        return jsonify({"error": str(exc), "real_ai": False}), 503


@app.get("/api/bids/<int:bid_id>/ai-analysis")
def get_ai_analysis(bid_id):
    bid = db.session.get(Bid, bid_id)
    if not bid:
        return jsonify({"error": "Bid not found"}), 404
    row = AIAnalysis.query.filter_by(bid_id=bid_id).order_by(AIAnalysis.created_at.desc()).first()
    if not row:
        return jsonify({"payload": None})
    row.payload = _normalize_deep_analysis(dict(row.payload or {}), bid)
    db.session.commit()
    return jsonify(row.to_dict())


@app.post("/api/ai/extract-requirements")
def ai_extract_requirements():
    data = request.get_json(silent=True) or {}
    tender_text = str(data.get("text", "")).strip()
    if len(tender_text) < 30:
        return jsonify({"error": "Provide tender text of at least 30 characters."}), 400
    system = """You extract procurement requirements from tender text. Return valid JSON only: {requirements:[{name,category,mandatory,interpretation,rule_type,field_name,numeric_bound,unit,evidence_required,source_clause,confidence}]}. Identify financial, technical, eligibility, document, experience, delivery and legal requirements. Never invent values. If a threshold is absent, keep numeric_bound null."""
    try:
        result = _groq_json("Extract and normalize all compliance requirements from this tender text:\n\n"+tender_text[:30000], system)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc), "real_ai": False}), 503


@app.post("/api/bids/<int:bid_id>/review-action")
def review_action(bid_id):
    bid = db.session.get(Bid, bid_id)
    if not bid:
        return jsonify({"error": "Bid not found"}), 404
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "")).upper()
    if action not in {"APPROVE", "REJECT", "SEND_FOR_REVIEW", "REQUEST_CLARIFICATION"}:
        return jsonify({"error": "Invalid review action"}), 400
    note = str(data.get("note", "")).strip()
    db.session.add(ReviewAction(bid_id=bid.id, action=action, note=note))
    if action == "APPROVE": bid.status = "COMPLIANT"
    elif action == "REJECT": bid.status = "NON_COMPLIANT"
    elif action in {"SEND_FOR_REVIEW", "REQUEST_CLARIFICATION"}: bid.status = "NEEDS_REVIEW"
    db.session.commit()
    return jsonify({"bid": bid.to_dict(detailed=True), "action": action})


@app.get("/api/bids/<int:bid_id>/audit")
def bid_audit(bid_id):
    bid = db.session.get(Bid, bid_id)
    if not bid:
        return jsonify({"error": "Bid not found"}), 404
    events = [{"action":"BID_RECEIVED","note":"Bid package registered","created_at":bid.submitted_at.isoformat() if bid.submitted_at else None}, {"action":"DOCUMENTS_EXTRACTED","note":f"{len(bid.documents)} document(s) processed","created_at":bid.submitted_at.isoformat() if bid.submitted_at else None}, {"action":"RULES_EVALUATED","note":f"Compliance score {bid.compliance_score}%","created_at":bid.submitted_at.isoformat() if bid.submitted_at else None}]
    for a in sorted(bid.review_actions, key=lambda x: x.created_at or 0): events.append(a.to_dict())
    return jsonify(events)

@app.get("/api/tenders")
def list_tenders():
    tenders = Tender.query.order_by(Tender.created_at.desc()).all()
    return jsonify([t.to_dict() for t in tenders])


@app.post("/api/tenders")
def create_tender():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()
    gem_bid_number = str(data.get("gem_bid_number", "")).strip()
    if not title or not gem_bid_number:
        return jsonify({"error": "title and gem_bid_number are required"}), 400
    if Tender.query.filter_by(gem_bid_number=gem_bid_number).first():
        return jsonify({"error": "A tender with this GeM bid number already exists"}), 409

    tender = Tender(
        title=title,
        gem_bid_number=gem_bid_number,
        department=str(data.get("department", "")).strip(),
        description=str(data.get("description", "")).strip(),
    )
    db.session.add(tender)
    db.session.flush()

    for req in data.get("requirements", []):
        name = str(req.get("name", "")).strip()
        rule_type = req.get("rule_type")
        if not name or rule_type not in {"DOCUMENT_PRESENT", "KEYWORD_MATCH", "MIN_VALUE", "MAX_VALUE"}:
            db.session.rollback()
            return jsonify({"error": "Each requirement needs a valid name and rule_type"}), 400
        db.session.add(Requirement(
            tender_id=tender.id,
            name=name,
            rule_type=rule_type,
            match_value=req.get("match_value"),
            field_name=req.get("field_name"),
            numeric_bound=req.get("numeric_bound"),
            weight=float(req.get("weight", 1.0) or 1.0),
            is_mandatory=bool(req.get("is_mandatory", True)),
        ))

    db.session.commit()
    return jsonify(tender.to_dict()), 201


@app.get("/api/tenders/<int:tender_id>")
def get_tender(tender_id):
    tender = db.session.get(Tender, tender_id)
    if not tender:
        return jsonify({"error": "Tender not found"}), 404
    return jsonify(tender.to_dict())


@app.get("/api/tenders/<int:tender_id>/bids")
def list_bids(tender_id):
    if not db.session.get(Tender, tender_id):
        return jsonify({"error": "Tender not found"}), 404
    bids = Bid.query.filter_by(tender_id=tender_id).order_by(Bid.compliance_score.desc().nullslast(), Bid.submitted_at.desc()).all()
    return jsonify([b.to_dict(detailed=True) for b in bids])


@app.post("/api/tenders/<int:tender_id>/bids")
def submit_bid(tender_id):
    tender = db.session.get(Tender, tender_id)
    if not tender:
        return jsonify({"error": "Tender not found"}), 404

    vendor_name = str(request.form.get("vendor_name", "")).strip()
    vendor_email = str(request.form.get("vendor_email", "")).strip()
    if not vendor_name:
        return jsonify({"error": "vendor_name is required"}), 400

    declared_values = {}
    for key, value in request.form.items():
        if key.startswith("declared_"):
            field = key.replace("declared_", "", 1)
            try:
                declared_values[field] = float(value)
            except (TypeError, ValueError):
                declared_values[field] = value

    bid = Bid(tender_id=tender.id, vendor_name=vendor_name, vendor_email=vendor_email, declared_values=declared_values)
    db.session.add(bid)
    db.session.flush()

    uploaded_count = 0
    for uploaded in request.files.getlist("documents"):
        if not uploaded or not uploaded.filename:
            continue
        safe_name = secure_filename(uploaded.filename)
        if not safe_name or "." not in safe_name or safe_name.rsplit(".", 1)[1].lower() not in ALLOWED_EXTENSIONS:
            db.session.rollback()
            return jsonify({"error": "Only PDF, DOCX, TXT and CSV documents are supported"}), 400
        stored_path = os.path.join(UPLOAD_DIR, f"bid{bid.id}_{safe_name}")
        uploaded.save(stored_path)
        text = extract_text(stored_path)
        db.session.add(BidDocument(bid_id=bid.id, filename=safe_name, stored_path=stored_path, extracted_text=text))
        uploaded_count += 1

    db.session.commit()
    evaluate_bid(bid)
    return jsonify(bid.to_dict(detailed=True)), 201


@app.get("/api/bids/<int:bid_id>")
def get_bid(bid_id):
    bid = db.session.get(Bid, bid_id)
    if not bid:
        return jsonify({"error": "Bid not found"}), 404
    return jsonify(bid.to_dict(detailed=True))


@app.post("/api/bids/<int:bid_id>/reevaluate")
def reevaluate_bid(bid_id):
    bid = db.session.get(Bid, bid_id)
    if not bid:
        return jsonify({"error": "Bid not found"}), 404
    evaluate_bid(bid)
    return jsonify(bid.to_dict(detailed=True))


@app.get("/api/tenders/<int:tender_id>/report")
def tender_report(tender_id):
    tender = db.session.get(Tender, tender_id)
    if not tender:
        return jsonify({"error": "Tender not found"}), 404
    bids = Bid.query.filter_by(tender_id=tender_id).order_by(Bid.compliance_score.desc().nullslast()).all()
    pdf = generate_tender_report(tender, bids)
    return send_file(__import__("io").BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
                     download_name=f"{secure_filename(tender.gem_bid_number)}_compliance_report.pdf")


@app.get("/api/bids/<int:bid_id>/report")
def bid_report(bid_id):
    bid = db.session.get(Bid, bid_id)
    if not bid:
        return jsonify({"error": "Bid not found"}), 404
    pdf = generate_bid_report(bid)
    return send_file(__import__("io").BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
                     download_name=f"bid_{bid.id}_compliance_certificate.pdf")


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": "Upload too large. Maximum request size is 20 MB."}), 413


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    app.logger.exception("Unhandled API error")
    return jsonify({"error": "Internal server error", "detail": str(error)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
