"""Pydantic schemas shared across services.

Phase A / P0 scaffold. Field sets follow the build plan; fill in
validators and constraints in subsequent steps.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


Confidence = Literal["High", "Medium", "Low"]
ExtractionConfidence = Literal["high", "medium", "low"]


SourceType = Literal[
    "structured_profile_json",
    "skillarbitrage_dossier_roadmap",
    "linkedin_optimization",
    "offer_blueprint",
    "upwork_profile",
    "linkedin_profile",
    "pricing_or_service_package",
    "portfolio_or_case_study",
    "testimonial_or_review",
    "certification_or_course",
    "discovery_call_transcript",
    "resume_or_cv",
    "past_proposal",
    "client_research",
    "niche_research",
    "personal_branding",
    "strategy_document",
    "notes_or_misc_profile_context",
    "generic_profile_document",
    "dossier_template_json",
    "unknown_supported_file",
]


SOURCE_PRIORITY: dict[str, int] = {
    "structured_profile_json": 1,
    "skillarbitrage_dossier_roadmap": 2,
    "linkedin_optimization": 3,
    "offer_blueprint": 4,
    "upwork_profile": 5,
    "linkedin_profile": 6,
    "pricing_or_service_package": 7,
    "portfolio_or_case_study": 8,
    "testimonial_or_review": 9,
    "certification_or_course": 10,
    "discovery_call_transcript": 11,
    "resume_or_cv": 12,
    "past_proposal": 13,
    "client_research": 14,
    "niche_research": 15,
    "personal_branding": 16,
    "strategy_document": 17,
    "notes_or_misc_profile_context": 18,
    "generic_profile_document": 19,
    "dossier_template_json": 20,
    "unknown_supported_file": 21,
}


SOURCE_TYPE_LABELS: dict[str, str] = {
    "structured_profile_json": "Structured profile JSON",
    "skillarbitrage_dossier_roadmap": "SkillArbitrage dossier / roadmap",
    "linkedin_optimization": "LinkedIn optimization document",
    "offer_blueprint": "Offer blueprint",
    "upwork_profile": "Upwork profile",
    "linkedin_profile": "LinkedIn profile",
    "pricing_or_service_package": "Pricing / service package",
    "portfolio_or_case_study": "Portfolio / case study",
    "testimonial_or_review": "Testimonial / review",
    "certification_or_course": "Certification / course",
    "discovery_call_transcript": "Discovery call transcript",
    "resume_or_cv": "Resume / CV",
    "past_proposal": "Past proposal",
    "client_research": "Client / target-client research",
    "niche_research": "Niche / market research",
    "personal_branding": "Personal branding",
    "strategy_document": "Strategy document",
    "notes_or_misc_profile_context": "Notes / misc profile context",
    "generic_profile_document": "Generic profile document",
    "dossier_template_json": "Dossier template JSON",
    "unknown_supported_file": "Unknown supported file",
}


ClaimType = Literal[
    "identity",
    "positioning",
    "selected_offer",
    "target_client",
    "service",
    "deliverable",
    "skill",
    "tool",
    "industry",
    "project",
    "experience",
    "work_history",
    "metric",
    "testimonial",
    "certification",
    "education",
    "language",
    "pricing",
    "availability",
    "proposal_preference",
    "weakness_or_constraint",
    "portfolio",
    "achievement",
    "location",
    "timezone",
    "guarantee",
    "other_relevant_evidence",
]


ExtractionStatus = Literal["ok", "partial", "failed", "metadata_only", "empty"]


ConflictStatus = Literal["none", "conflicting", "superseded", "supporting"]


UsedFor = Literal["recommendation", "proposal", "missing_info", "ignored"]


class ExtractedField(BaseModel):
    name: str
    value: Optional[str] = None
    confidence: Confidence = "Low"
    visible: bool = True


FieldSource = Literal[
    "not visible",
    "ocr extracted",
    "user corrected",
    "manually entered",
]


class ScreenshotField(BaseModel):
    value: str = "Not visible"
    confidence: ExtractionConfidence = "low"
    source: str = "not visible"


class ConfirmedField(BaseModel):
    name: str
    value: str = "Not visible"
    confidence: ExtractionConfidence = "low"
    source: FieldSource = "not visible"


class ScreenshotExtraction(BaseModel):
    job_title: ScreenshotField = Field(default_factory=ScreenshotField)
    job_description: ScreenshotField = Field(default_factory=ScreenshotField)
    client_need: ScreenshotField = Field(default_factory=ScreenshotField)
    required_deliverables: ScreenshotField = Field(default_factory=ScreenshotField)
    required_skills: ScreenshotField = Field(default_factory=ScreenshotField)
    budget_or_rate: ScreenshotField = Field(default_factory=ScreenshotField)
    project_type: ScreenshotField = Field(default_factory=ScreenshotField)
    experience_level: ScreenshotField = Field(default_factory=ScreenshotField)
    project_duration: ScreenshotField = Field(default_factory=ScreenshotField)
    posted_date: ScreenshotField = Field(default_factory=ScreenshotField)
    proposal_count: ScreenshotField = Field(default_factory=ScreenshotField)
    payment_verification: ScreenshotField = Field(default_factory=ScreenshotField)
    client_rating: ScreenshotField = Field(default_factory=ScreenshotField)
    client_total_spend: ScreenshotField = Field(default_factory=ScreenshotField)
    hire_rate: ScreenshotField = Field(default_factory=ScreenshotField)
    client_location: ScreenshotField = Field(default_factory=ScreenshotField)
    connects_required: ScreenshotField = Field(default_factory=ScreenshotField)


class JobOpportunity(BaseModel):
    title: Optional[str] = None
    client_need: Optional[str] = None
    budget: Optional[str] = None
    proposal_count: Optional[int] = None
    required_skills: list[str] = Field(default_factory=list)
    client_quality: Optional[str] = None
    client_questions: list[str] = Field(default_factory=list)
    fields: list[ExtractedField] = Field(default_factory=list)


class DossierFile(BaseModel):
    path: str
    kind: str
    modified_at: Optional[str] = None
    readable: bool = True


class EvidenceItem(BaseModel):
    claim: str
    source_file: str
    location: str
    kind: str


class ChunkRecord(BaseModel):
    chunk_id: str
    file_name: str
    file_path: str
    file_type: str
    source_type: SourceType
    source_priority: int
    page_number: Optional[int] = None
    section_name: Optional[str] = None
    extracted_text: str = ""
    json_data: Optional[Any] = None
    extraction_status: ExtractionStatus = "ok"
    extraction_warning: Optional[str] = None


class ProofPoint(BaseModel):
    evidence_id: str
    source_file: str
    source_type: SourceType
    source_priority: int
    source_location: str
    claim_type: ClaimType
    claim_text: str
    normalized_value: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    confidence: ExtractionConfidence = "medium"
    conflict_status: ConflictStatus = "none"
    used_for: UsedFor = "proposal"

    @property
    def claim(self) -> str:
        """Spec-aligned alias for ``claim_text``."""
        return self.claim_text


class CanonicalProfileField(BaseModel):
    value: Any = None
    evidence_ids: list[str] = Field(default_factory=list)
    source_confidence: ExtractionConfidence = "low"
    conflict_note: Optional[str] = None


class CanonicalFreelancerProfile(BaseModel):
    name: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    title_or_positioning: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    location: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    timezone: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    languages: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    selected_offer: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    guarantee: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    target_client: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    industries: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    services: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    deliverables: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    skills: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    tools: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    work_history: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    education: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    certifications: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    portfolio_or_proof: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    achievements: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    pricing: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    preferred_project_types: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    proposal_preferences: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    strengths: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    weaknesses_to_account_for: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    missing_information: CanonicalProfileField = Field(default_factory=CanonicalProfileField)
    source_summary: CanonicalProfileField = Field(default_factory=CanonicalProfileField)


MatchLevel = Literal["direct", "adjacent", "weak", "missing"]
ProofRating = Literal["strong", "medium", "weak", "unknown"]


class OpportunityProfile(BaseModel):
    """Compact, normalized view of one uploaded Upwork opportunity.

    Built from the confirmed job fields before matching/scoring so the
    LLM and the deterministic rules both reason about the same compact
    structure rather than the raw screenshot fields.
    """

    opportunity_title: Optional[str] = None
    client_problem: Optional[str] = None
    required_skills: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    required_deliverables: Optional[str] = None
    industry_or_domain: Optional[str] = None
    expected_experience_level: Optional[str] = None
    budget_or_rate: Optional[str] = None
    proposal_count: Optional[str] = None
    client_quality_indicators: dict[str, str] = Field(default_factory=dict)
    visible_risks: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


class RequiredSkillMatch(BaseModel):
    """One opportunity requirement compared against the evidence index."""

    requirement: str = ""
    match_level: MatchLevel = "missing"
    matching_evidence_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class PortfolioProofAnalysis(BaseModel):
    """LLM signal describing what real proof supports the opportunity.

    ``score_signal`` is a 0-100 *signal only* — the final numeric
    Portfolio Proof score is computed deterministically in
    :mod:`app.services.scoring`, never taken directly from the LLM.
    """

    rating: ProofRating = "unknown"
    score_signal: int = 0
    direct_proof: list[str] = Field(default_factory=list)
    adjacent_proof: list[str] = Field(default_factory=list)
    missing_proof: list[str] = Field(default_factory=list)
    matched_portfolio_items: list[str] = Field(default_factory=list)
    matched_projects: list[str] = Field(default_factory=list)
    matched_testimonials: list[str] = Field(default_factory=list)
    matched_work_history: list[str] = Field(default_factory=list)
    matched_skills: list[str] = Field(default_factory=list)
    matched_tools: list[str] = Field(default_factory=list)
    evidence_ids_used: list[str] = Field(default_factory=list)
    short_reason: str = ""
    confidence: ExtractionConfidence = "low"


class ScoreComponent(BaseModel):
    """One weighted score component plus its explanation.

    ``value``/``max_value`` are the points awarded out of the component
    weight; ``short_reason`` is user-facing; ``evidence_ids_used`` are
    shown only behind the debug panel; ``source`` records how the value
    was derived.
    """

    value: int = 0
    max_value: int = 0
    short_reason: str = ""
    evidence_ids_used: list[str] = Field(default_factory=list)
    confidence: ExtractionConfidence = "low"
    source: str = "llm_match_result + deterministic_scoring"


class SubScores(BaseModel):
    profile_fit: int = 0
    portfolio_proof: int = 0
    client_quality: int = 0
    competition: int = 0
    budget_value: int = 0


class ScoreResult(BaseModel):
    total: int
    sub_scores: SubScores
    confidence: Confidence
    job_fingerprint: str = ""
    components: dict[str, ScoreComponent] = Field(default_factory=dict)


BeginnerResult = Literal["Apply Confidently", "Proceed With Caution", "Do Not Proceed"]
PaymentStatus = Literal["verified", "not_verified", "not_visible"]
ProposalBucket = Literal["low", "high", "too_high", "not_visible"]
PostedBucket = Literal["fresh", "recent", "stale", "not_visible"]
RatingBucket = Literal["ok", "low", "not_visible"]
ExperienceBucket = Literal["entry", "intermediate", "expert", "other", "not_visible"]


class BeginnerWarning(BaseModel):
    key: str
    reason: str


class BeginnerJobEvaluation(BaseModel):
    """Result of the beginner-safety checklist for one opportunity.

    Produced deterministically by
    :func:`app.services.beginner_evaluator.evaluate` (the service returns a
    plain dict; this model documents and, where useful, validates the
    shape). ``result`` plus up to two ``reasons`` are the only fields shown
    on the clean UI — the per-field buckets are debug-only.
    """

    result: BeginnerResult = "Proceed With Caution"
    instant_no: bool = False
    reasons: list[str] = Field(default_factory=list, max_length=2)
    warnings: list[BeginnerWarning] = Field(default_factory=list)
    instant_no_reasons: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    missing_info_note: Optional[str] = None
    reduce_confidence: bool = False
    triggered_rule: str = ""
    score_signals: dict[str, bool] = Field(default_factory=dict)
    fields: dict[str, Any] = Field(default_factory=dict)


class Recommendation(BaseModel):
    verdict: Literal["Strongly Proceed", "Proceed", "Proceed with Caution", "Do Not Proceed"]
    short_verdict: str = ""
    why: str = ""
    match_strengths: list[str] = Field(default_factory=list, max_length=2)
    concerns: list[str] = Field(default_factory=list, max_length=2)
    connects_recommendation: Optional[str] = None
    best_proposal_angle: Optional[str] = None

    # Backwards-compat aliases used by older callers / tests.
    one_line: str = ""
    reasoning: str = ""
    strengths: list[str] = Field(default_factory=list)
    connect_guidance: Optional[str] = None
    proposal_angle: Optional[str] = None
