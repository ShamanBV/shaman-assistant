# Documentation Gap Analysis Report

**Generated from:** 1167 Slack questions, 4340 Intercom questions

---

# B2B SaaS Support Documentation Coverage Analysis
## Shaman Platform - Pharmaceutical Content Management

## 1. TOPIC CLUSTERING

### **Veeva Integration & Export Issues** (35+ questions)
- **Examples:**
  - "Does that help with the CLM export to Veeva?" 
  - "Some destination presentation have not been created in Vault yet and will be ignored"
  - "Links are not working in Vault CRM but work fine in Shaman"
- **Urgency:** HIGH - Daily occurrence, blocks customer workflows

### **MLR (Medical Legal Regulatory) Process** (25+ questions)
- **Examples:**
  - "How do I set the MLR to approved?"
  - "I need to link it with the existing approval document in MLR. Correct?"
  - "How can I get rid off fragments that are coming from a previous email that I duplicate?"
- **Urgency:** HIGH - Critical compliance requirement

### **Visual Builder & Content Creation** (20+ questions)
- **Examples:**
  - "Is there a way to avoid this grid appearing on top of the image when uploaded in VB?"
  - "What type and subtype is better to use when editing image in Shaman?"
  - "Could you please remind me how scroll functionality is done in Shaman?"
- **Urgency:** MEDIUM-HIGH - Core functionality questions

### **Email Template Issues & Testing** (18+ questions)
- **Examples:**
  - "In the test email the timer that I put is not appearing"
  - "The blue box at the bottom and MLR ID do not show up on test email"
  - "Email is cutting early in the middle of an image on Veeva"
- **Urgency:** HIGH - Affects customer deliverables

### **Account Management & Permissions** (15+ questions)
- **Examples:**
  - "Do I need permission from a BO on this account before making the change?"
  - "Could you add the Adymna Product for CRM Product field?"
  - "I don't have access to Veeva Promomats, what do I need to do?"
- **Urgency:** MEDIUM - Administrative overhead

### **Content Synchronization & Hub** (12+ questions)
- **Examples:**
  - "What is the delay for Design Template to appear in Country Account?"
  - "Will it be deleted automatically from Shaman Country accounts?"
  - "I would like to see the Cosentyx content in the Hub"
- **Urgency:** MEDIUM - Content distribution issues

### **Technical Performance & Errors** (10+ questions)
- **Examples:**
  - "I'm facing heavy performance issues with a 207 slide presentation"
  - "Width must be a number error appears when uploading Video file"
  - "Code 401 is displayed when trying to add a video"
- **Urgency:** HIGH - System functionality

### **CLM Navigation & Linking** (8+ questions)
- **Examples:**
  - "Do you have any instructions on how to link to pop-up slides?"
  - "Order set in Shaman is not respected in Veeva Desktop CRM"
  - "How to navigate to slides using template.html logic?"
- **Urgency:** MEDIUM - Feature usage

### **Content Library & Asset Management** (8+ questions)
- **Examples:**
  - "How to upload new slides to the content library?"
  - "Is it only print type that is not being saved into visual library?"
  - "Do icons need to be separate svg files or one svg file?"
- **Urgency:** MEDIUM - Content organization

### **Localization & Multi-language** (5+ questions)
- **Examples:**
  - "What solution for utm_campaign parameter in accounts where templates are created in multiple languages?"
  - "Could you help me duplicate one AE template from CZ to SK?"
- **Urgency:** LOW-MEDIUM - Specific use cases

## 2. DOCUMENTATION COVERAGE MATRIX

| Topic | Coverage | Existing Sources | Gap Description |
|-------|----------|-----------------|-----------------|
| **Veeva Integration & Export** | Partial | Help Center (iVA creation), some Confluence | Missing troubleshooting guides, error resolution, specific integration scenarios |
| **MLR Process** | Partial | PDF guides, some Help Center | Missing workflow diagrams, approval states, error handling |
| **Visual Builder** | Good | Help Center (30+ articles), PDF guides | Missing advanced features, troubleshooting, file format guidelines |
| **Email Testing & Preview** | Partial | Best practices docs | Missing comprehensive testing guide, client-specific rendering issues |
| **Account Management** | None | Scattered in Confluence | No centralized permission/access management guide |
| **Content Synchronization** | Partial | Hub documentation | Missing timing, delay explanations, sync troubleshooting |
| **Technical Errors** | None | No centralized source | Missing error code dictionary, troubleshooting flowcharts |
| **CLM Navigation** | Partial | Some Help Center articles | Missing advanced linking, desktop vs mobile differences |
| **Content Library** | Partial | Help Center basics | Missing advanced organization, file type specifications |
| **Localization** | None | Basic Hub docs | Missing multi-language workflows, content duplication processes |

## 3. PRIORITY GAPS

1. **Veeva Integration Troubleshooting** - Most frequent customer blocker - Suggested doc: **"Veeva Integration Error Resolution Guide"**

2. **MLR Workflow & States** - Critical compliance process unclear - Suggested doc: **"Complete MLR Process Flowchart with Troubleshooting"**

3. **Error Code Dictionary** - Technical errors cause escalations - Suggested doc: **"Shaman Error Codes & Solutions Reference"**

4. **Email Rendering Issues** - Affects final deliverables - Suggested doc: **"Email Testing & Client Compatibility Guide"**

5. **Account Permissions Matrix** - Administrative overhead - Suggested doc: **"User Roles & Permissions Quick Reference"**

6. **Content Sync Timing** - Confusion about delays - Suggested doc: **"Hub Synchronization: Timing & Troubleshooting"**

7. **File Format Specifications** - Upload issues frequent - Suggested doc: **"Supported File Formats & Size Limits by Feature"**

8. **Desktop vs Mobile CLM Differences** - Platform-specific issues - Suggested doc: **"CLM Platform Compatibility Matrix"**

9. **Visual Builder Advanced Features** - Power user needs - Suggested doc: **"Visual Builder Advanced Techniques & Limitations"**

10. **Content Duplication Workflows** - Localization efficiency - Suggested doc: **"Content Localization & Duplication Best Practices"**

## 4. QUICK WINS

1. **Create Error Code Lookup Table** - Extract all error messages from Slack/Intercom and create searchable reference
2. **MLR Status Diagram** - Simple flowchart showing approval states and transitions
3. **File Upload Specifications Sheet** - One-page reference with size limits, formats, and feature compatibility
4. **Veeva Integration Checklist** - Step-by-step pre-export validation list
5. **Account Permission Quick Cards** - Visual reference showing what each role can/cannot do

## 5. RECOMMENDED DOCUMENTATION STRUCTURE

```
📁 SHAMAN SUPPORT DOCUMENTATION
├── 🚨 TROUBLESHOOTING (New Priority Section)
│   ├── Error Code Dictionary
│   ├── Veeva Integration Issues
│   ├── Email Rendering Problems
│   └── Performance & Technical Issues
│
├── 🔄 WORKFLOWS & PROCESSES
│   ├── MLR Complete Guide (Enhanced)
│   ├── Content Sync & Hub Management
│   ├── Account Setup & Permissions
│   └── Content Localization Workflows
│
├── 🛠️ FEATURE GUIDES (Existing, Enhanced)
│   ├── Visual Builder (Add Advanced Techniques)
│   ├── Email Builder (Add Testing Guide)
│   ├── CLM Builder (Add Platform Compatibility)
│   └── Content Library (Add Organization Best Practices)
│
├── 📋 QUICK REFERENCE
│   ├── File Format Specifications
│   ├── Platform Compatibility Matrix
│   ├── Permission & Role Matrix
│   └── Integration Checklists
│
└── 🎯 USE CASES & EXAMPLES
    ├── Customer-Specific Scenarios
    ├── Advanced Feature Combinations
    └── Common Workflow Patterns
```

**Implementation Priority:**
1. **Week 1-2:** Quick wins (Error codes, MLR diagram, File specs)
2. **Week 3-4:** Veeva integration troubleshooting guide
3. **Month 2:** Enhanced workflow documentation
4. **Month 3:** Advanced feature guides and use cases

This structure addresses the high-frequency support issues while building on existing documentation strength in basic feature explanations.