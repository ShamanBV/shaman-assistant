# Intent Classifier Analysis

Based on 524 product questions, 616 QA/bug reports, and 11030 Intercom questions.

# Analysis of B2B SaaS Support Questions

## 1. COMMON PATTERNS BY CATEGORY

### PRODUCT QUESTIONS (How-to/Feature Inquiries)
- **Permission/Access patterns**: "Do I need permission from...", "Can you please take a look", "does anyone know why..."
- **Feature availability**: "Is it possible to...", "Do you know if [feature] is available", "Can we..."
- **Configuration questions**: "how to navigate to slides", "enable/disable [setting]", "adjust fonts names"
- **Account-specific differences**: "why is it not showing in the customer account, but it does on the test"
- **Veeva integration**: "Clickstream events", "CLM presentations", "Smart Update", "sync", "export to vault"

### QA/BUG REPORTS
- **Sync issues**: "sync hours", "Message Tags are being synced", "resync the document"
- **Field behavior**: "non-mandatory fields are currently being treated as required"
- **Button/UI states**: "Create button remains inactive", "toggles also works fine"
- **Error reproduction**: "I have cleared cache", "can user try to resync", "Should be fixed"
- **Account-specific bugs**: mentions specific account names + problem description

### INTERCOM (Customer Escalations)
- **Token rendering**: Custom tokens not displaying properly in email templates
- **Cross-language support**: French customers asking questions in French
- **CRM integration issues**: "links are not working", "presentations have not been created in Vault yet"
- **PDF generation problems**: "error when trying to download the PDF"
- **Testing requests**: "Could you please test", "let me know if it works"

## 2. MISSING INTENTS

**Suggested new categories:**
- **sync_issue**: Specific to synchronization problems between Shaman and Veeva
- **account_setup**: Questions about account configuration, user permissions, policy setup
- **template_issue**: Problems with email/presentation templates specifically
- **integration_question**: Questions about third-party integrations (Veeva, CRM systems)
- **token_rendering**: Custom token display issues (very common in French account)

## 3. HIGH-CONFIDENCE PATTERNS

```
"does anyone know" → how_to (0.95)
"Can you please take a look" → escalation (0.9)
"Should be fixed, can user try to resync?" → bug_product (0.95)
"sync" + "hours/time" → sync_issue (0.9)
"Create button remains inactive" → bug_product (0.95)
"Message Tags are being synced" → sync_issue (0.9)
"error when trying to download" → bug_product (0.9)
"tokens are rendering properly" → template_issue (0.9)
"presentations have not been created in Vault" → integration_question (0.9)
```

## 4. AMBIGUOUS PATTERNS

```
"Do you have any news?" → Could be follow-up on bug, feature request, or general inquiry
"Can you help me?" → Too generic - could be any category
"Please take another look" → Could be bug follow-up or how-to clarification  
"Is it the expected behaviour" → Could be bug report or how-to question
"What am I missing?" → Could be configuration issue or user error
```

## 5. ENTITY PATTERNS

### Features
- **Naming**: CamelCase or hyphenated ("Message Tags", "One off use", "pinch to exit", "CLM presentations")
- **Technical terms**: "Clickstream", "Smart Update", "Veeva labels", "Visual Builder"

### Customers/Accounts  
- **Format**: "Company + Region + Department" (e.g., "Novartis UK IMM", "GSK Brazil", "Galderma Alpine Aesthetics")
- **References**: Account numbers in parentheses, user IDs, Intercom conversation links

### Errors
- **Sentry links**: Full URLs to error tracking
- **Error messages**: Quoted strings like "Width must be a number"
- **HTTP exceptions**: Technical error class names

### Urgency Indicators
- **High**: "urgent", "OOO" (out of office), "sorry to chase you", multiple follow-ups
- **Medium**: "when you have time", "please take a look"
- **Time-sensitive**: References to specific dates, "before Monday"

## 6. SUGGESTED CLASSIFIER PROMPT IMPROVEMENTS

**Add this context to your intent classifier:**

```
DOMAIN-SPECIFIC CONTEXT:
- "Sync" issues are very common - relate to Shaman↔Veeva synchronization
- Account names follow pattern: Company + Region + Product Area  
- "Vault" refers to Veeva Vault document management system
- French customers often ask in French - treat as same intent as English
- "CLM" = Closed Loop Marketing presentations
- "AE" = Approved Email templates
- "MLR" = Medical, Legal, Regulatory review process

URGENCY SIGNALS:
- Multiple @ mentions = escalation
- "OOO" (out of office) = escalation  
- Conversation links = customer escalation
- "Should be fixed" = bug confirmation/testing

TECHNICAL PATTERNS:
- Error messages in quotes = bug_product
- Sentry URLs = bug_product  
- "resync" suggestions = sync_issue
- "policy" + "not working" = bug_config
- Token syntax {{customText[...]}} = template_issue
```

**Key insight**: This is a technical support system where internal team members help each other AND handle customer escalations. The same technical issues appear in both contexts, but customer issues need more careful handling and follow-up.