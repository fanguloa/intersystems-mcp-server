---
name: uifw-cds-card
description: 'Render CDS Hooks cards for clinical decision support using @intersystems/cds-card. USE FOR: CDS Hooks card display, clinical decision support alerts, clinician feedback collection, CDS card view/feedback modes. DO NOT USE FOR: generic cards/panels, notification banners (use uifw-notification), non-CDS alerts.'
argument-hint: 'Describe the CDS card you need (view mode, feedback mode, card data structure)'
---

# @intersystems/cds-card

Render CDS Hooks cards for clinical decision support. Supports view-only mode (preview) and feedback mode (interactive form for accepting/overriding suggestions).

## Quick Start

```typescript
// app.module.ts
import { CdsCardModule } from '@intersystems/cds-card';

@NgModule({
  imports: [CdsCardModule]
})
export class AppModule { }
```

```html
<!-- View mode (preview only) -->
<fr-cds-card [cardInfo]="cardInfo"></fr-cds-card>

<!-- Feedback mode (interactive with form) -->
<fr-cds-card 
  [cardInfo]="cardInfo" 
  [feedbackMode]="true"
  (feedbackSubmitted)="handleFeedback($event)">
</fr-cds-card>
```

## CdsCardComponent API

**Inputs:**
- `cardInfo: Card` - Card data following CDS Hooks spec (required)
- `feedbackMode: boolean` - Enable interactive feedback form (default: false)

**Outputs:**
- `feedbackSubmitted: EventEmitter<FeedbackPayload>` - Emits when user acknowledges, overrides, or submits

## View vs Feedback Mode

**View Mode** (`feedbackMode=false`):
- Display-only preview of CDS card
- Shows indicator, summary, detail, source, suggestions, and links
- No user interaction beyond close button

**Feedback Mode** (`feedbackMode=true`):
- Full interactive form using isc-form
- Users can accept suggestions, select override reasons, or provide free-text feedback
- Emits `feedbackSubmitted` with structured payload for CDS Hooks endpoint

## Card Data Structure (CDS Hooks Spec)

```typescript
interface Card {
  uuid?: string;
  Name: string;                                    // Card identifier
  summary?: string;                                // Brief summary displayed prominently
  detail?: string;                                 // Detailed description
  indicator?: 'info' | 'warning' | 'critical';     // Visual urgency (default: 'info')
  source?: Source;                                 // Card source metadata
  suggestions?: Suggestion[];                      // Actionable suggestions
  selectionBehavior?: 'at-most-one' | 'any';       // Selection constraint
  links?: Link[];                                  // External resources
  overrideReasons?: OverrideReason[];              // Predefined override reasons
}

interface Suggestion {
  label: string;
  uuid?: string;
  isRecommended?: boolean;
}
```

## Feedback Payload

```typescript
interface FeedbackPayload {
  card: string;                              // Card UUID
  outcome: 'accepted' | 'overridden';
  acceptedSuggestions?: { id: string }[];    // For 'accepted' outcome
  overrideReason?: {
    reason?: { code: string; system: string };
    userComment?: string;                    // Free-text explanation
  };
  outcomeTimestamp: string;                  // ISO timestamp (UTC)
}
```

## isc-form Integration

Feedback mode uses custom isc-form field types:
- `suggestions` - Multi-select for accepting suggestions
- `overrides` - Select for override reasons
- `overrideComment` - Textarea for free-text feedback

The form dynamically adjusts button labels (Acknowledge/Override/Submit/Ignore) based on user selections.

## Key Imports

```typescript
import { CdsCardModule } from '@intersystems/cds-card';
import { Card, FeedbackPayload } from '@intersystems/cds-card';
```
