import re
from models import db, ComplianceResult


def _parse_number(raw):
    if raw is None:
        return None
    s = str(raw).strip().lower().replace("₹", "").replace("rs.", "").replace("rs", "").replace(",", "").strip()
    try:
        m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
        if not m:
            return None
        n = float(m.group())
        if "crore" in s or re.search(r"\bcr\b", s):
            return n * 10000000
        if "lakh" in s or "lac" in s:
            return n * 100000
        return n
    except (AttributeError, ValueError):
        return None


def _norm_name(value):
    s = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    # Remove common corporate suffixes so small formatting differences do not create false mismatches.
    s = re.sub(r"\b(private limited|pvt ltd|pvt limited|private ltd|limited|ltd|llp)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _extract_explicit_metadata(text):
    """Extract explicit identity/reference fields for cross-document consistency checks."""
    t = text or ""
    names = []
    refs = []
    name_patterns = [
        r"(?:legal\s+name|company\s+name|vendor|bidder|applicant)\s*[:=\-]\s*([^\n\r]{3,120})",
    ]
    ref_patterns = [
        r"(?:gem\s+)?bid\s+(?:number|no\.?|reference|ref(?:erence)?)\s*[:=\-]\s*([A-Z0-9/\-]{6,80})",
        r"(?:bid\s+reference|reference\s+number)\s*[:=\-]\s*([A-Z0-9/\-]{6,80})",
    ]
    for p in name_patterns:
        names.extend(m.group(1).strip(" .,:;") for m in re.finditer(p, t, re.I))
    for p in ref_patterns:
        refs.extend(m.group(1).strip(" .,:;") for m in re.finditer(p, t, re.I))
    return names, refs


def _extract_document_values(text):
    t = text or ""
    values = {}
    turnover_patterns = [
        r"(?:latest\s+annual\s+turnover|annual\s+turnover|turnover)\s*[:=\-]?\s*(₹?\s*[\d,]+(?:\.\d+)?\s*(?:lakh|lac|crore|cr)?)",
        r"turnover\s+(?:of|is)\s*(₹?\s*[\d,]+(?:\.\d+)?\s*(?:lakh|lac|crore|cr)?)"
    ]
    for p in turnover_patterns:
        m = re.search(p, t, re.I)
        if m:
            n = _parse_number(m.group(1))
            if n is not None:
                values["annual_turnover"] = n
                break
    exp_patterns = [
        r"(?:relevant\s+)?experience\s*(?:years?)?\s*[:=\-]\s*(\d+(?:\.\d+)?)\s*years?",
        r"experience\s+(?:of|for)\s+(\d+(?:\.\d+)?)\s*years?",
        r"(\d+(?:\.\d+)?)\s+years?\s+(?:of\s+)?(?:relevant\s+)?experience"
    ]
    for p in exp_patterns:
        m = re.search(p, t, re.I)
        if m:
            values["experience_years"] = float(m.group(1))
            break
    return values


def evaluate_bid(bid):
    """Evidence-first evaluation with cross-document identity/reference validation."""
    ComplianceResult.query.filter_by(bid_id=bid.id).delete()
    docs = list(bid.documents or [])
    combined_text = "\n".join((d.extracted_text or "") for d in docs)
    combined_lower = combined_text.lower()
    filenames = " ".join((d.filename or "") for d in docs).lower()

    declared = dict(bid.declared_values or {})
    extracted = _extract_document_values(combined_text)
    # Uploaded evidence takes precedence over manually declared values for exact constraints.
    for field, value in extracted.items():
        declared[field] = value
    bid.declared_values = declared

    # Cross-document consistency signals. Only explicit fields are checked; absence is not a mismatch.
    vendor_name = _norm_name(bid.vendor_name)
    explicit_names = []
    explicit_refs = []
    for d in docs:
        names, refs = _extract_explicit_metadata(d.extracted_text or "")
        explicit_names.extend((d.filename or "document", n) for n in names if n)
        explicit_refs.extend((d.filename or "document", r) for r in refs if r)

    identity_mismatches = [(fn, n) for fn, n in explicit_names if vendor_name and _norm_name(n) and _norm_name(n) != vendor_name]
    expected_ref = str(getattr(bid.tender, "gem_bid_number", "") or "").strip().upper()
    ref_mismatches = [(fn, r) for fn, r in explicit_refs if expected_ref and str(r).strip().upper() != expected_ref]

    total_weight = 0.0
    earned_weight = 0.0
    mandatory_failed = False

    for req in bid.tender.requirements:
        passed = False
        detail = ""
        weight = max(float(req.weight or 0), 0.0)
        name_lower = (req.name or "").lower()

        if req.rule_type == "DOCUMENT_PRESENT":
            keyword = (req.match_value or "").strip().lower()
            aliases = [keyword]
            if "gst" in name_lower:
                aliases += ["gst", "tax registration"]
            if "emd" in name_lower:
                aliases += ["emd", "earnest money", "payment receipt"]
            matched = next((a for a in aliases if a and (a in combined_lower or a in filenames)), None)
            passed = matched is not None
            detail = f"Evidence found using '{matched}'" if passed else f"Could not find evidence for '{req.name}' in submitted documents"

            # A present document is not sufficient when its explicit identity/reference contradicts the bid.
            if passed and "gst" in name_lower and identity_mismatches:
                passed = False
                fn, n = identity_mismatches[0]
                detail = f"GST evidence found in {fn}, but vendor identity '{n}' does not match bidder '{bid.vendor_name}'"
            if passed and "emd" in name_lower:
                if identity_mismatches:
                    passed = False
                    fn, n = identity_mismatches[0]
                    detail = f"EMD evidence has bidder identity '{n}', which does not match '{bid.vendor_name}'"
                elif ref_mismatches:
                    passed = False
                    fn, r = ref_mismatches[0]
                    detail = f"EMD evidence in {fn} references '{r}', expected '{expected_ref}'"

        elif req.rule_type == "KEYWORD_MATCH":
            keyword = (req.match_value or "").strip().lower()
            passed = bool(keyword) and keyword in combined_lower
            detail = f"Evidence keyword '{req.match_value}' found" if passed else f"Evidence keyword '{req.match_value}' not found"

        elif req.rule_type in {"MIN_VALUE", "MAX_VALUE"}:
            val = declared.get(req.field_name)
            if val is not None and req.numeric_bound is not None:
                numeric_val = _parse_number(val)
                bound = float(req.numeric_bound)
                if numeric_val is not None:
                    if req.rule_type == "MIN_VALUE":
                        passed = numeric_val >= bound
                        detail = f"{req.field_name}={numeric_val:g}, required >= {bound:g}"
                    else:
                        passed = numeric_val <= bound
                        detail = f"{req.field_name}={numeric_val:g}, required <= {bound:g}"
                    # If a declaration conflicts with extracted evidence, never let the declaration silently win.
                    original_declared = bid.declared_values.get(f"__submitted_{req.field_name}") if isinstance(bid.declared_values, dict) else None
                    if original_declared not in (None, ""):
                        declared_num = _parse_number(original_declared)
                        if declared_num is not None and abs(declared_num - numeric_val) > max(1.0, abs(numeric_val) * 0.01):
                            passed = False if req.is_mandatory else passed
                            detail += f"; conflict with submitted declaration ({declared_num:g})"
                else:
                    detail = f"Invalid numeric value for '{req.field_name}'"
            else:
                detail = f"Missing evidence/value for '{req.field_name}'"

        db.session.add(ComplianceResult(
            bid_id=bid.id, requirement_id=req.id, requirement_name=req.name,
            passed=passed, detail=detail, weight=weight, is_mandatory=req.is_mandatory
        ))
        total_weight += weight
        if passed:
            earned_weight += weight
        elif req.is_mandatory:
            mandatory_failed = True

    bid.compliance_score = round((earned_weight / total_weight) * 100, 2) if total_weight else 0.0
    if mandatory_failed:
        bid.status = "NON_COMPLIANT"
    elif bid.compliance_score >= 85:
        bid.status = "COMPLIANT"
    else:
        bid.status = "NEEDS_REVIEW"
    db.session.commit()
    return bid
