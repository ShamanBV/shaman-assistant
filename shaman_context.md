# Shaman Platform Context

## 1. What Shaman Is (Core Definition)

Shaman is a content authoring platform for the pharmaceutical industry, designed to create, localize, manage, and update approved multichannel marketing and medical content.

Shaman is a **guided self-service authoring platform** for end users such as brand marketers and digital managers, instead of agency designers and developers. This makes local pharma teams more agile, flexible, faster and it saves budget.

### What does guided self-service mean?

- **Author content** in a self-service capability
- **Offer a structured, simplified process** for authoring between reusable components and Veeva PromoMats for review and approval that is easy for end users to understand
- **Automates and simplifies steps** by integrating with Veeva, respecting MLR status from Veeva in Shaman

**Guided** means we add additional guardrails for content authoring:

| Phase | Guardrails |
|-------|------------|
| Pre-authoring | Provide design templates, possibly with locked elements |
| Authoring | Respect brand design system, limit brand colors, fonts, images, styling of buttons etc, add content cards, manage references |
| Post-authoring | Shaman compliance assistants check Quality of content, Language and compare against previous approved content (MLR compare) |

Key capabilities:
- Reuse approved components across channels
- Maintain traceability between content, references, and approvals
- Export content compliantly to execution systems (Veeva CRM, SFMC, web, etc.)

### Veeva Vault PromoMats Integration

Shaman is deeply integrated with Veeva Vault PromoMats for:
- MLR (Medical, Legal, Regulatory) review and approval
- Version alignment between authoring and approved assets
- Export of approved content for downstream activation
- Import of Components (images), Claims, references and modular content during authoring

---

## 2. Shaman Builders (Authoring Capabilities)

Shaman consists of multiple builders, each optimized for a specific content channel but sharing a common content, design, and compliance model.

### Builders Overview

| Subdomain | Builder | Purpose |
|-----------|---------|---------|
| AE | Approved Email Builder | Create Veeva Approved Emails for compliant HCP communication |
| ME | Marketing Email Builder | Create non-MLR / pre-MLR marketing emails, typically exported to SFMC |
| CLM | CLM Builder | Assemble interactive HTML CLM presentations for Veeva CRM |
| SC | Slide Builder | Create, edit and manage individual slides from templates or imported PDF, that can be approved and used in CLM Builder |
| VA | Visual Asset Builder | Create, edit and manage images, banners, graphics, print PDFs, that can be approved and exported to visual library or export as file (eg banner) |
| WEB | Landing Page Builder | Create HTML landing pages, similar in behavior to email builders |
| CC | Smart Content Cards | Create pre-approved modular components (text, images, references) that can be used in other builders |

---

## 3. Standard Content Lifecycle (Applies to All Builders)

Each builder follows a consistent lifecycle, allowing AI systems to reason across builders.

### 3.1 Create (New / Hub)

Create content from:
- New > select Template
- Duplicate content
- Go to Hub to find content from other Shaman accounts and translate and duplicate

Content is scoped to an account and builder.

### 3.2 Compose Text (Magic Copy)

Only when New (else content is already there):
- Insert content via Magic Copy (AI-assisted text generation)

### 3.3 Design

For new Content start with: select a start template

Author content in editor:
- Access visual builder to add images and icons
- Add blocks
- Add tokens (email only)
- Adhere to brand design system
- Add smart content cards
- Manage references (in email now)
- Check content with Compliance assistants

### 3.4 Test & Validate

- Preview rendering
- Validate links, references, and assets

### 3.5 MLR (If Applicable)

Submit to Veeva Vault PromoMats

Maintain:
- Version linkage
- Annotation alignment
- Design-to-MLR traceability

### 3.6 Export / Publish

| Builder | Export Target |
|---------|---------------|
| AE & CLM | Export to Veeva Vault PromoMats |
| ME | Export to Salesforce Marketing Cloud |
| WEB / VA / CC | Export as ZIP or HTML/PDF packages |

---

## 4. Content Hub & Sharing Model

Shaman includes a Hub for sharing content between isolated accounts.

### Sharing final composite assets

Sharing approved content across:
- Global accounts (Management account, Global accounts)
- Local accounts

### Sharing global templates and images

- Via Management or Global accounts
- Sharing happens via brand and countries, based on Veeva IDs

This supports global-local operating models common in pharma.

---

## 5. Shared Visual Library (Across Builders)

All builders access a visual library, ensuring content reuse.

### Visual Sources

- Global image library (shared across accounts)
- Local uploads (account-specific)
- Icon library (account group)
- Veeva images (integration brings in images when user selects)

---

## 6. Smart Content Cards (CC) – Modular Content

Smart Content Cards (CC) are Shaman's modular backbone.

### A CC can contain:

- Text assets (headlines, body copy, CTA)
- Image assets
- References and citations
- Persona & segment applicability
- Pre-approval status

### CCs can be:

- Approved once
- Inserted into AE, ME, CLM, WEB
- Updated centrally (with governance)

Smart content cards can be integrated with Veeva modular content.

This enables true modular content reuse aligned with Veeva principles.

---

## 7. System Architecture & Security Model

### Account Isolation

Each Shaman account is:
- Logically isolated
- Backed by encrypted RDS
- Uses encrypted S3 buckets

**No cross-account data leakage**

Customer accounts are usually created:
- Per country; eg: Almirall - Spain (Almirall-ES)
- Per country and therapeutic area; eg: Takeda Germany Onco (Takeda-DE-Onco)

### Account Groups

All accounts of one customer are in an account group to allow:
- Shared content
- Shared templates
- Shared settings

Still preserves data boundaries.

### Users

- Users are maintained in a userpool per account group
- Users can be added to individual accounts to give them access per account - via the user management module in the account
- First time user is added via the account, they will be added to the pool
- When added in other accounts, system attaches them from pool to the account
- The account switch at the top can be used to switch between accounts
- Userpool is AWS Cognito which supports SSO

### Superadmin Backend

ConfigOps and Product are Superadmin users and they can configure:

**Per accounts:**
- All features
- Veeva configuration - like Veeva entities
- Settings per builder

**Per account group:**
- Shared settings
- Shared features
- Shared values (like brands etc) and Veeva entities
- Userpool

---

## 8. Feature Configuration Model

Shaman has a single code base. All configuration and functionality in Shaman is:
- Feature-flag driven
- Configured per account
- Often scoped per builder

This allows:
- Gradual rollouts
- Risk control
- Customer-specific configurations
- AI-safe reasoning (features may or may not exist)

---

## 9. How an AI Should Reason About Shaman (RAG Guidance)

When answering questions about Shaman, an AI should assume:

### Not all builders are equal

- ME ≠ AE ≠ CLM
- MA, AE and Web have same HTML editor
- Slide and Visual have same canvas editor
- CLM has assembler
- Builder can have different tabs / functionality
- Builders do not have the same maturity level in features > email is most advanced

### MLR only applies where enabled

- Usually AE, ME and CLM or slides

### Export targets differ by builder

### Content is modular, not flat

### Approval ≠ creation

### Feature availability is account-specific

---

## One-Sentence Summary (for embeddings)

Shaman is a modular, multi-builder pharma content authoring platform with deep Veeva Vault PromoMats integration, enabling compliant creation, reuse, approval, and export of multichannel content across emails, CLM, web, visuals, and modular content cards.
