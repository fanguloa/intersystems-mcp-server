---
name: uifw-template-question-library
description: 'Manage reusable question libraries for template-builder using @intersystems/template-question-library. USE FOR: question library browsing, reusable question management, importing library questions into templates, question categorization. DO NOT USE FOR: standalone forms (use uifw-isc-form), template state management (use uifw-template-builder), form rendering.'
argument-hint: 'Describe how you want to use the question library (browsing, importing, managing questions)'
---

# @intersystems/template-question-library

Build and manage reusable question libraries for dynamic template creation. Provides a searchable table interface for browsing, creating, editing, and viewing questions that can be imported into templates.

## Quick Start

```typescript
import { QuestionLibraryModule } from '@intersystems/template-question-library';

@NgModule({
  imports: [QuestionLibraryModule]
})
export class AppModule {}
```

```html
<template-question-library
  [questionsListData]="questions"
  [questionFDN]="viewQuestionFDN"
  [questionSpec]="editQuestionSpec"
  [isQuestionFDNReady]="isFDNReady"
  [isQuestionSpecReady]="isSpecReady"
  (viewIconClicked)="onViewQuestion($event)"
  (editIconClicked)="onEditQuestion($event)"
  (deleteButtonClicked)="onDeleteQuestion($event)"
  (saveButtonClicked)="onSaveQuestion($event)"
  (createButtonClicked)="onCreateQuestion($event)"
  (cancelButtonClicked)="onCancel()"
  (addQuestionButtonClicked)="onAddQuestion()">
</template-question-library>
```

## Component: template-question-library

Main component providing a searchable table of library questions with view/edit/delete actions.

### Inputs
- `questionsListData: any[]` - Array of question objects to display (with Id, QuestionText, Type)
- `questionFDN: FDN` - Form Definition (FDN) for viewing question in read-only mode
- `questionSpec: IQuestion` - Question specification from @intersystems/template-builder for editing
- `isQuestionFDNReady: boolean` - Flag indicating view FDN is ready
- `isQuestionSpecReady: boolean` - Flag indicating edit spec is ready

### Outputs
- `viewIconClicked: EventEmitter<string>` - Emits question ID when view icon clicked
- `editIconClicked: EventEmitter<string>` - Emits question ID when edit icon clicked
- `deleteButtonClicked: EventEmitter<string>` - Emits question ID when delete confirmed
- `saveButtonClicked: EventEmitter<any>` - Emits updated question when save clicked (edit mode)
- `createButtonClicked: EventEmitter<any>` - Emits new question when create clicked
- `cancelButtonClicked: EventEmitter<any>` - Emits when cancel clicked
- `addQuestionButtonClicked: EventEmitter<any>` - Emits when "Add Question" button clicked

## Question Types (QuestionType enum)

- `TEXT_PARAGRAPH` - Multi-line textarea
- `SINGLE_TEXT_LINE` - Single-line text input
- `NUMBER` - Numeric input (supports MinRange/MaxRange validation)
- `TABLE` - Embedded table with configurable columns
- `MULTI_CHECK_BOX` - Multiple selection checkboxes
- `RADIO_BUTTON` - Single selection radio buttons
- `DROP_DOWN` - Select dropdown
- `DATE` - Date picker

## Internal Components

### EditQuestionComponent
Form for creating/editing questions. Supports all question types including complex table questions with multiple columns. Includes XSS validation, option management, and number range validation.

### QuestionDetailComponent
Read-only display of question details using isc-form in view mode.

### BlankQuestionComponent
Placeholder shown when no question is selected.

## Integration with template-builder

Questions use `IQuestion` and `IField` interfaces from @intersystems/template-builder:

```typescript
import { IQuestion, IField } from '@intersystems/template-builder';

interface IQuestion {
  Id: string;
  Prompt: string;
  Type: string; // 'field' or 'table'
  Description?: string; // for table type
  Fields: IField[];
  IsRequired?: boolean;
  IsCommentEnabled?: boolean;
}

interface IField {
  Id: string;
  Label: string;
  InputType: QuestionType;
  IsRequired: boolean;
  Options?: Array<{Name: string, Value: string, Id?: string}>;
  MinRange?: number; // for number type
  MaxRange?: number; // for number type
}
```

## Key Imports

```typescript
import { QuestionLibraryModule } from '@intersystems/template-question-library';
import { QuestionType, SubWindow } from '@intersystems/template-question-library';
```

Required peer dependencies: @intersystems/table, @intersystems/isc-form, @intersystems/template-builder, @ngx-formly/core, @ngx-translate/core.
