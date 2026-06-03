# Upwork Proposal Strategist Build Plan v2.0

Document type: Build plan, planning only, not implementation  
Prepared for: Team Content, SkillArbitrage  
Primary users: AI for Women, US Accounting, Startup Generalist, Strategic HR, Academic Writing, AI Automation cohorts

## Product Intent

The tool helps freelancers avoid wasting Upwork connects on poor-fit opportunities and, when a job is worth pursuing, generate a grounded proposal that does not fabricate experience.

The core bet is simple: a freelancer with limited connects should apply to fewer, higher-fit jobs with stronger personalized proposals instead of spending connects broadly on generic applications.

The product is intentionally local-first. Original dossier files stay on the user's machine. The configured LLM API is used only for AI execution such as OCR, analysis, scoring narrative, proposal drafting, and verification.

## End-to-End Flow

```text
API check
-> Privacy disclosure
-> Local dossier folder selected
-> Folder validation
-> Upwork screenshot upload
-> Vision/OCR extraction
-> User confirms extracted fields
-> Evidence index creation
-> Match analysis
-> Score and confidence generation
-> Recommendation shown
-> User explicitly clicks Generate Proposal
-> Draft proposal from evidence
-> Verification pass
-> Final output displayed
```

The key design principle is that uncertain or untrusted data must be surfaced before it affects the recommendation. Scoring never runs on unconfirmed OCR fields, and proposals never render until factual claims are checked against dossier evidence.

## Core Components

1. API Gate validates the API key, text generation, vision support, quota, and selected model before downstream screens are enabled.
2. Dossier Reader reads local PDF, DOCX, and image files from the folder path supplied by the user.
3. Folder Validator reports readable files, unsupported files, modified dates, and a dossier strength score.
4. Screenshot Parser extracts job information from one or more Upwork screenshots using a vision model.
5. Field Confirmation Layer shows extracted fields and confidence flags to the user for correction before scoring.
6. Evidence Index stores source-backed proof points from the dossier with file name, location, and claim text.
7. Match Engine compares confirmed job data against evidence across fit, proof, competition, client quality, budget, and risk.
8. Scoring and Confidence Module returns a 100-point score plus High, Medium, or Low confidence.
9. Recommendation Layer maps score and confidence to a verdict with a short explanation.
10. Proposal Generator runs only after an explicit user action and uses only evidence-index content.
11. Verification Pass checks every factual proposal claim before display.

## Inputs

### API Configuration

Required before the tool does anything else.

Supported error states:

- `NO API ADDED`
- `INVALID API KEY`
- `API QUOTA EXCEEDED`
- `API CONNECTION FAILED`
- `VISION MODEL NOT AVAILABLE`
- `MODEL NOT CONFIGURED`

### Local Dossier Folder

Strong dossier signals include:

- Upwork profile PDF
- Resume or CV
- Portfolio links and screenshots
- Case studies with numbers
- Testimonials with attribution
- Past winning proposals

Weak or risky inputs include generic profiles, unsupported file formats, unproven self-written claims, and files with no outcomes or attribution.

### Upwork Opportunity Screenshots

Screenshots are uploaded from the user's local machine. Multiple screenshots per opportunity may be supported so client details, job description, questions, and attachments can be captured separately.

Missing fields are marked `Not visible`. The system must not guess hidden information.

## Data Handling

What stays local:

- Original dossier files
- Folder paths and local file structure
- Screenshot files after session exit
- Calibration and outcome tracking data, if enabled

What may be sent to the LLM API:

- Selected extracted dossier text
- Screenshot/OCR field values
- Portfolio snippets
- Testimonials or work history snippets required for matching and drafting

What gets logged:

- API call metadata only: timestamps, model, token counts, and error codes
- Never raw dossier text, screenshot content, or generated proposals
- Logs rotate after 7 days

Optional redaction mode should strip emails, phone numbers, addresses, and client names before API calls.

## Fourteen-Step Workflow

1. API setup and capability test.
2. Privacy and data flow disclosure accepted.
3. Dossier folder selected and validated.
4. Screenshot or screenshots uploaded.
5. Vision/OCR extraction.
6. User confirms or edits extracted fields.
7. Evidence index created.
8. Match engine runs.
9. Score and confidence level generated.
10. Recommendation shown.
11. User clicks Generate Proposal.
12. Proposal drafted from evidence.
13. Verification pass.
14. Final output displayed.

## Match Dimensions

The match engine evaluates nine dimensions:

- Skill fit
- Industry fit
- Experience level
- Portfolio proof
- Budget fit
- Competition
- Client quality
- Differentiator angle
- Risk level

## Scoring Model

The recommendation score is out of 100:

| Dimension | Weight | Purpose |
| --- | ---: | --- |
| Profile Fit | 30 | How well the freelancer's skills and experience match the job. |
| Portfolio Proof | 20 | Whether real examples, results, or samples support credibility. |
| Client Quality | 20 | Payment verification, spend history, hire rate, rating, and requirement clarity. |
| Competition | 15 | Proposal count, time since posting, and differentiator strength. |
| Budget / Value | 15 | Whether the opportunity is worth the connects and effort. |

Confidence prevents a precise score from creating false certainty:

| Confidence | Trigger | UX Behavior |
| --- | --- | --- |
| High | Critical job fields visible and confirmed; dossier strength above 70. | Score shown normally and verdict trusted. |
| Medium | One or two critical fields missing or dossier strength 40 to 70. | Score shown with caution and user prompted to improve inputs. |
| Low | Three or more critical fields missing or dossier strength below 40. | Verdict softened by one tier and user encouraged to improve dossier or screenshot. |

Unknown fields are neutral for scoring but reduce confidence. Missing information is uncertainty, not proof of a bad job.

## Verdict Mapping

| Score | Verdict | Meaning |
| ---: | --- | --- |
| 80-100 | Strongly Proceed | High fit, strong evidence, reasonable competition. Apply with priority. |
| 65-79 | Proceed | Solid fit with manageable gaps. Worth the connects. |
| 50-64 | Proceed with Caution | Mixed signals. Apply only if the differentiator is real. |
| Below 50 | Do Not Proceed | Save the connects. Better opportunities exist. |

Override rules:

- Exceptional fit can overcome high competition.
- Vague but high-value jobs should trigger caution, not automatic rejection.
- Low confidence softens the verdict by one tier regardless of numeric score.

## Output Structure

The tool produces seven output sections:

1. Opportunity Snapshot: title, client need, budget, proposal count, client quality, required skills, and confidence flags.
2. Fit and Opportunity Score: five sub-scores, total score, confidence level, and recommendation.
3. Recommendation Summary: one-line verdict, two-line reasoning, strengths, concerns, connect guidance, and proposal angle.
4. Evidence Available: relevant dossier proof points with source references.
5. Proposal: gated until the user clicks Generate Proposal.
6. Client Question Answers: shown only if the screenshot includes client questions.
7. Missing Information: dossier or screenshot gaps that weakened scoring or proposal quality.

Proposal length targets:

| Job Type | Target Length |
| --- | ---: |
| Simple | 100-150 words |
| Standard | 150-250 words |
| Complex high-value | 250-350 words |

Quick proposal actions:

- Shorter
- More confident
- More conversational
- Add proof
- Remove unsupported claim

Every regeneration must run through verification before display.

## AI Safety Controls

### Anti-Fabrication

The tool must never invent:

- Past clients
- Project names
- Testimonials
- Specific metrics
- Certifications
- Skills
- Hidden screenshot fields

When evidence is thin, the tool should use honest broader phrasing, reduce proposal length, lean on approach and tool fluency, and list the gap in Missing Information.

### Prompt Injection Defense

Dossier files, screenshots, OCR text, job descriptions, and portfolio content are untrusted input. They are data, not instructions.

Enforcement:

- Wrap source content in explicit data markers.
- Keep system rules above source content.
- Ignore instruction-like text inside resumes, screenshots, or job descriptions.
- Use verification to catch drift.

### Evidence Verification

Before the final proposal is displayed, verify:

- Named clients, projects, and employers
- Numbers, percentages, and metrics
- Tool, platform, or certification claims
- Industry experience or years of work

Unsupported claims are removed, softened, or flagged as missing information.

## UI Plan

### Screen 1: Setup and Capability Test

API key input, capability test, precise error state handling, and first-run privacy disclosure.

### Screen 2: Dossier Path and Screenshot Upload

Left side: local dossier folder path and folder validation feedback.  
Right side: screenshot upload.

Analyze button remains disabled until required inputs are present.

### Screen 3: Field Confirmation

Shows the extracted screenshot fields with confidence flags and edit controls. User must confirm before scoring.

This prevents OCR errors such as proposal count, budget, client spend, or required skills being misread and silently affecting the verdict.

### Screen 4: Analysis Output

Displays the seven-part output, score bar, confidence badge, recommendation banner, and gated Generate Proposal action.

For Do Not Proceed verdicts, proposal generation requires an extra confirmation checkbox.

## Recommended Tech Stack

| Layer | Recommended Choice |
| --- | --- |
| Frontend | Streamlit |
| Primary LLM | Claude Sonnet 4 |
| Fallback LLM | GPT-4-class fallback |
| Vision | Claude Vision or GPT-4 Vision |
| Dossier parsing | `pdfplumber`, `python-docx`, Pillow |
| Evidence index | SQLite or in-memory dictionary |
| Storage | Local filesystem only |
| API key storage | `.env` or encrypted local config |
| Logs | Metadata only, 7-day rotation |
| Outcome tracking | Local SQLite |
| Deployment | Local machine |

## Development Phases

### Phase A: P0 Before Feature Work

- API gate with capability test
- Privacy and data flow disclosure
- Untrusted-input rule in system prompt
- Field confirmation screen

### Phase B: P1 Before Pilot Release

- Evidence index
- Verification pass
- Confidence scoring
- Folder validation

### Phase C: P2 Before Public Rollout

- Safe fallback behavior
- Local outcome tracking
- Optional redaction mode
- Quick-action proposal editing
- Multi-screenshot support
- Pilot rollout to one cohort

## Key Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| OCR misreads job data | Field confirmation plus per-field confidence flags. |
| Dossier is too thin | Folder validation, Missing Information output, shorter honest proposals. |
| LLM fabricates claims | Evidence index, anti-fabrication prompt rules, verification pass. |
| Prompt injection | Treat all source content as untrusted data. |
| Users ignore bad-fit verdicts | Extra friction and local click tracking for Do Not Proceed proposal generation. |
| Score appears too certain | Add confidence level and soften Low-confidence verdicts. |
| Sensitive data sent to API | Disclosure, provider transparency, optional redaction mode. |
| Logs capture sensitive content | Metadata-only logging and code review checks. |

## Success Metrics

| Metric | Target |
| --- | ---: |
| Reply rate lift | 2x |
| Connect ROI | 3x improvement |
| Decision adherence for Do Not Proceed verdicts | 70%+ |
| Time from screenshot to submit-ready proposal | Under 4 minutes |
| Confidence calibration | Win rates should rise from Low to Medium to High confidence |

## Explicit Non-Scope

- Upwork API integration
- Auto-submission of proposals
- Cloud storage of dossier or screenshots
- Multi-user accounts
- Pricing or bid optimization
- Multi-language proposals
- Cross-user learning
- Integration with existing SkillArbitrage tools
- Fetching portfolio link contents by default

## Implementation Notes

- P0 gates should be implemented before proposal-writing features.
- Field confirmation is the main control against OCR-driven bad scoring.
- Evidence index and verification are the main controls against fabricated proposals.
- Recommendation copy should be concise at the headline level, with deeper reasoning hidden behind expansion.
- The interface should surface uncertainty instead of smoothing it away.
- Generated proposal content should be shorter when evidence is thin rather than padded with generic filler.
