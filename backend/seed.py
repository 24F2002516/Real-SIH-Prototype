"""Run with: python seed.py  (after app.py has created the db once, or standalone)"""
from app import app, db
from models import Tender, Requirement

with app.app_context():
    db.create_all()

    if Tender.query.filter_by(gem_bid_number="GEM/2026/B/000123").first():
        print("Sample tender already exists, skipping seed.")
    else:
        tender = Tender(
            title="Supply of Industrial Valves for Refinery Maintenance",
            gem_bid_number="GEM/2026/B/000123",
            department="Ministry of Petroleum & Natural Gas",
            description="Sample tender to demo the bid compliance verification prototype.",
        )
        db.session.add(tender)
        db.session.flush()

        requirements = [
            Requirement(tender_id=tender.id, name="GST Registration Certificate",
                        rule_type="DOCUMENT_PRESENT", match_value="gst", weight=2, is_mandatory=True),
            Requirement(tender_id=tender.id, name="EMD Payment Receipt",
                        rule_type="DOCUMENT_PRESENT", match_value="emd", weight=2, is_mandatory=True),
            Requirement(tender_id=tender.id, name="Experience Certificate mentions relevant work",
                        rule_type="KEYWORD_MATCH", match_value="valve", weight=1.5, is_mandatory=False),
            Requirement(tender_id=tender.id, name="Minimum Annual Turnover (₹50 Lakh)",
                        rule_type="MIN_VALUE", field_name="annual_turnover", numeric_bound=5000000,
                        weight=2, is_mandatory=True),
            Requirement(tender_id=tender.id, name="Minimum 3 Years Experience",
                        rule_type="MIN_VALUE", field_name="experience_years", numeric_bound=3,
                        weight=1.5, is_mandatory=True),
        ]
        db.session.add_all(requirements)
        db.session.commit()
        print(f"Seeded tender id={tender.id} with {len(requirements)} requirements.")
