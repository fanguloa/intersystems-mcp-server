---
name: uifw-template-builder
description: 'Build template/form editors with ngrx state management using @intersystems/template-builder. USE FOR: template editing UIs, form/questionnaire builders, managing template state with ngrx, undo/redo via metaReducers, drag-and-drop question/section ordering. DO NOT USE FOR: rendering forms (use uifw-isc-form), simple CRUD UIs, question library management (use uifw-template-question-library).'
argument-hint: 'Describe the template builder you need (template type, editing features, state requirements)'
---

# Template Builder — Form/Template Editor

The `@intersystems/template-builder` library provides a complete template editing system with ngrx state management. It allows building complex form/questionnaire editors with sections, questions, fields, validation, and conditional logic.

## Quick Start

```typescript
import { BuilderModule, builderReducer, metaReducers, TEMPLATE_BUILDER_STATE_KEY } from '@intersystems/template-builder';

@NgModule({
  imports: [
    StoreModule.forRoot({}),
    StoreModule.forFeature(TEMPLATE_BUILDER_STATE_KEY, builderReducer, {
      metaReducers: metaReducers
    }),
    BuilderModule
  ]
})
```

The library exports a pre-configured reducer and metaReducers for state clearing/undo functionality.

## Architecture

Template-builder provides the editing layer for templates that are rendered by `@intersystems/isc-form`. It integrates with `@intersystems/template-question-library` for reusable questions.

**Key concepts:**
- **Templates** contain sections (sectioned) or questions (non-sectioned)
- **Sections** group related questions with enable/disable logic
- **Questions** have prompts, types (field/table), and fields
- **Fields** define input types (text, select, date, number) and validation
- **Normalized state** using normalizr for efficient nested updates
- **Validation** at template/section/question/field levels with error propagation

## Features

| Feature | Reference |
|---------|-----------|
| ngrx setup (reducer, actions, selectors, metaReducers) | [ngrx-setup.md](./references/ngrx-setup.md) |
| Builder configuration, template structure, extensibility | [builder-config.md](./references/builder-config.md) |

## Key Imports

```typescript
// Core module
import { BuilderModule } from '@intersystems/template-builder';

// Store setup
import { 
  builderReducer, 
  metaReducers, 
  TEMPLATE_BUILDER_STATE_KEY 
} from '@intersystems/template-builder';

// Actions
import { 
  AddQuestion, 
  AddSection, 
  UpdateQuestionById, 
  UpdateSectionById,
  SwapQuestions, 
  SwapSections,
  UpdateTitle,
  GetTemplateSuccess,
  resetTemplate 
} from '@intersystems/template-builder';

// Selectors
import { 
  selectActiveTemplate, 
  selectActiveTemplateNormalized,
  selectQuestionById,
  selectSectionById 
} from '@intersystems/template-builder';

// Interfaces
import { 
  ITemplate, 
  ISection, 
  IQuestion, 
  IField,
  normalizedData,
  AppState 
} from '@intersystems/template-builder';

// Services
import { 
  TemplatesService, 
  TemplatesCustomEntitiesService 
} from '@intersystems/template-builder';

// Components (for embedding)
import { EditTemplateComponent } from '@intersystems/template-builder';
```

## Common Patterns

**Load template into store:**
```typescript
const processed = this.templatesService.processTemplate(template);
this.store.dispatch(GetTemplateSuccess({ template: processed.template }));
this.store.dispatch(GetTemplateSuccessCacheDeletedQuestions({ 
  deletedQuestions: processed.deletedQuestionsCache 
}));
```

**Add question:**
```typescript
const newQuestion = this.templatesService.getNewTemplateQuestion();
this.store.dispatch(AddQuestion({ question: newQuestion }));
```

**Custom reducer:**
```typescript
import { iscTemplateReducerOns, resetTemplate } from '@intersystems/template-builder';

export const templateReducer = createReducer(
  initialTemplateState,
  ...iscTemplateReducerOns,  // Include all built-in actions
  on(resetTemplate, (state, payload) => customResetLogic(state, payload)),
  on(YourCustomAction, (state, payload) => customLogic(state, payload))
);
```
