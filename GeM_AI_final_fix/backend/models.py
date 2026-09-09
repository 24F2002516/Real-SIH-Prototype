from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

RULE_TYPES = ("DOCUMENT_PRESENT", "KEYWORD_MATCH", "MIN_VALUE", "MAX_VALUE")


class Tender(db.Model):
    __tablename__ = "tenders"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    gem_bid_number = db.Column(db.String(80), unique=True, nullable=False)
    department = db.Column(db.String(150))
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    requirements = db.relationship("Requirement", backref="tender", cascade="all, delete-orphan")
    bids = db.relationship("Bid", backref="tender", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "gem_bid_number": self.gem_bid_number,
            "department": self.department,
            "description": self.description,
            "requirements": [r.to_dict() for r in self.requirements],
            "bid_count": len(self.bids),
        }


class Requirement(db.Model):
    __tablename__ = "requirements"
    id = db.Column(db.Integer, primary_key=True)
    tender_id = db.Column(db.Integer, db.ForeignKey("tenders.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    rule_type = db.Column(db.String(30), nullable=False)  # see RULE_TYPES
    # keyword used for DOCUMENT_PRESENT / KEYWORD_MATCH matching (case-insensitive substring/regex)
    match_value = db.Column(db.String(300))
    # field name in Bid.declared_values used for MIN_VALUE / MAX_VALUE
    field_name = db.Column(db.String(100))
    numeric_bound = db.Column(db.Float)
    weight = db.Column(db.Float, default=1.0)
    is_mandatory = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "rule_type": self.rule_type,
            "match_value": self.match_value,
            "field_name": self.field_name,
            "numeric_bound": self.numeric_bound,
            "weight": self.weight,
            "is_mandatory": self.is_mandatory,
        }


class Bid(db.Model):
    __tablename__ = "bids"
    id = db.Column(db.Integer, primary_key=True)
    tender_id = db.Column(db.Integer, db.ForeignKey("tenders.id"), nullable=False)
    vendor_name = db.Column(db.String(200), nullable=False)
    vendor_email = db.Column(db.String(200))
    declared_values = db.Column(db.JSON, default=dict)  # e.g. {"annual_turnover": 5200000, "experience_years": 4}
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)

    compliance_score = db.Column(db.Float)   # 0-100, filled after evaluation
    status = db.Column(db.String(30), default="PENDING")  # PENDING / COMPLIANT / NON_COMPLIANT / NEEDS_REVIEW

    documents = db.relationship("BidDocument", backref="bid", cascade="all, delete-orphan")
    results = db.relationship("ComplianceResult", backref="bid", cascade="all, delete-orphan")

    def to_dict(self, detailed=False):
        base = {
            "id": self.id,
            "tender_id": self.tender_id,
            "vendor_name": self.vendor_name,
            "vendor_email": self.vendor_email,
            "declared_values": self.declared_values,
            "compliance_score": self.compliance_score,
            "status": self.status,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "documents": [d.filename for d in self.documents],
        }
        if detailed:
            base["results"] = [r.to_dict() for r in self.results]
        return base


class BidDocument(db.Model):
    __tablename__ = "bid_documents"
    id = db.Column(db.Integer, primary_key=True)
    bid_id = db.Column(db.Integer, db.ForeignKey("bids.id"), nullable=False)
    filename = db.Column(db.String(300))
    stored_path = db.Column(db.String(500))
    extracted_text = db.Column(db.Text)


class ComplianceResult(db.Model):
    __tablename__ = "compliance_results"
    id = db.Column(db.Integer, primary_key=True)
    bid_id = db.Column(db.Integer, db.ForeignKey("bids.id"), nullable=False)
    requirement_id = db.Column(db.Integer, db.ForeignKey("requirements.id"), nullable=False)
    requirement_name = db.Column(db.String(200))
    passed = db.Column(db.Boolean)
    detail = db.Column(db.String(400))
    weight = db.Column(db.Float)
    is_mandatory = db.Column(db.Boolean)

    def to_dict(self):
        return {
            "requirement_id": self.requirement_id,
            "requirement_name": self.requirement_name,
            "passed": self.passed,
            "detail": self.detail,
            "weight": self.weight,
            "is_mandatory": self.is_mandatory,
        }


class AIAnalysis(db.Model):
    __tablename__ = "ai_analyses"
    id = db.Column(db.Integer, primary_key=True)
    bid_id = db.Column(db.Integer, db.ForeignKey("bids.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    payload = db.Column(db.JSON, default=dict)

    bid = db.relationship("Bid", backref=db.backref("ai_analyses", cascade="all, delete-orphan"))

    def to_dict(self):
        return {"id": self.id, "bid_id": self.bid_id, "created_at": self.created_at.isoformat() if self.created_at else None, "payload": self.payload or {}}


class ReviewAction(db.Model):
    __tablename__ = "review_actions"
    id = db.Column(db.Integer, primary_key=True)
    bid_id = db.Column(db.Integer, db.ForeignKey("bids.id"), nullable=False)
    action = db.Column(db.String(40), nullable=False)
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    bid = db.relationship("Bid", backref=db.backref("review_actions", cascade="all, delete-orphan"))

    def to_dict(self):
        return {"id": self.id, "bid_id": self.bid_id, "action": self.action, "note": self.note, "created_at": self.created_at.isoformat() if self.created_at else None}
