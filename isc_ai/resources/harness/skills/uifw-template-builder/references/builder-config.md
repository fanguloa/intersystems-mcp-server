# Builder Configuration — Template Structure and Extensibility

## Template Structure

Templates follow a hierarchical structure:

```
ITemplate
  ├─ Sections[] (optional, for sectioned templates)
  │   └─ Questions[]
  │       └─ Fields[]
  │           └─ Options[] (for select/radio/checkbox)
  └─ Questions[] (for non-sectioned templates)
      └─ Fields[]
          └─ Options[]
```

### ITemplate
```typescript
interface ITemplate {
  Id: ID_TYPE;
  Identifier: string;
  Name: string;
  Title: string;
  Summary: string;
  Questions?: IQuestion[];       // Non-sectioned
  Sections?: ISection[];         // Sectioned
  Status: 'D' | 'P';             // Draft | Published
  Version: number;
  CustomPDFFooterTemplate?: string;
  XSLTemplate?: string;
  DocumentTypeId?: string;
  UI_MODEL?: TemplateUIModel;
}

interface TemplateUIModel {
  questionValidationErrors: QuestionValidationErrors;
  sectionValidationErrors: SectionValidationErrors;
}
```

### ISection
```typescript
interface ISection {
  Id: ID_TYPE;
  Identifier: string;
  Title: string;
  Description: string;
  Questions: IQuestion[];
  Action: string;                  // 'delete' | 'edit' | ''
  Collapsed?: boolean;
  EnableBehavior?: EnableBehavior;
  UI_MODEL?: SectionUIModel;
}

interface SectionUIModel {
  validationErrors: string[];
  questionValidationErrors: QuestionValidationErrors;
  rejectDropIfNotSupported?: boolean;
  rejectDropInvalid?: boolean;
  rejectDropRepeated?: boolean;
}
```

### IQuestion
```typescript
interface IQuestion {
  Id: ID_TYPE;
  Identifier: string;
  Prompt: string;
  Type: string;                    // 'field' | 'table'
  Description: string;
  Fields: IField[];
  Action: string;                  // 'delete' | 'edit' | ''
  IsRequired: boolean;
  IsMultiAnswerEnabled: boolean;
  IsCommentEnabled: boolean;
  EnableBehavior?: EnableBehavior;
  UI_MODEL?: QuestionUIModel;
}

interface QuestionUIModel {
  validationErrors: string[];
  fieldValidationErrors: { [fieldId: string]: string[] };
  isLibraryQuestion?: boolean;
  parentSection?: ID_TYPE | null;
  rejectDropIfNotSupported?: boolean;
  rejectDropInvalid?: boolean;
  rejectDropRepeated?: boolean;
}
```

### IField
```typescript
interface IField {
  Id: ID_TYPE;
  Identifier: string;
  Label: string;
  DataType: 'string' | 'boolean' | 'number';
  InputType: 'select' | 'multiCheckbox' | 'radio' | 'checkbox' | 
             'textline' | 'textarea' | 'date' | 'number' | string;
  Hints: string;
  Options: IOption[] | null;
  MinRange?: number | string;
  MaxRange?: number | string;
  MinDate?: string;
  MaxDate?: string;
  MaxLength?: number;
  CodeTable?: string;
  Collapsed: boolean;
  IsRequired?: boolean;
  IsReadOnly?: boolean;
  ShowAllOptions?: boolean;
  UI_MODEL?: {
    validationErrors: string[];
    cachedOptions: IOption[];
  };
}
```

### IOption
```typescript
interface IOption {
  Id?: ID_TYPE;
  Name: string;
  Value: string;
  System?: string;
}
```

## Conditional Logic (EnableBehavior)

Questions and sections support conditional visibility based on other questions' values:

```typescript
interface EnableBehavior {
  TriggerFor: TriggerFor[];       // Entities this triggers
  TriggerOn: TriggerOnOperand;    // Condition to show this entity
}

interface TriggerOnOperand {
  Identifier: string;              // Question/section identifier
  Value: any;                      // Target value
  ComparisonOperator: 'eq' | 'gt' | 'lt' | 'ct' | '';
  LogicalOperator: 'and' | 'or' | '';
  Operands: TriggerOnOperand[];    // Nested conditions
}

interface TriggerFor {
  Identifier: string;
  Type: 'option' | 'number' | 'string' | string;
  Value: any;
}
```

## TemplatesService — Core Utilities

The `TemplatesService` provides template manipulation and validation:

**Factory methods:**
```typescript
getNewTemplateWithNoSections(): ITemplate
getNewTemplateWithSections(): ITemplate
getNewTemplateSection(): ISection
getNewTemplateQuestion(): IQuestion
getNewTemplateField(): IField
```

**Processing:**
```typescript
processTemplate(template: ITemplate): FilteredTemplateWithDeletedQuestions
// Filters deleted questions, sets up UI_MODELs, converts numeric values to strings

denormalizeTemplateData(type: 'section' | 'question', normalizedData): any
getReconstructedTemplate(templateShell: ITemplate, normalizedData): ITemplate
```

**Validation:**
```typescript
validate(type: string, payload: any): { errors: string[] }
// Types: 'option' | 'options' | 'field' | 'table' | 'question' | 'ranges' | 
//        'section' | 'template' | 'date_ranges' | 'max_length'

showValidationErrorsPopups(errors: string[])
```

**State checks:**
```typescript
isTemplateObjectIdTemporary(id: ID_TYPE): boolean
isQuestionSaved(question: IQuestion): boolean
isQuestionDeleted(question: IQuestion): boolean
isQuestionSlatedForDeletion(question: IQuestion): boolean
isQuestionExemptFromValidation(question: IQuestion): boolean
isSectionEmpty(section: ISection): boolean
isTemplateEmpty(template: ITemplate): boolean
doesEntityHaveLogic(entity: IQuestion | ISection): boolean
```

**Question types:**
```typescript
isQuestionTypeTable(type: string): boolean
isQuestionTypeNumber(type: string): boolean
isQuestionTypeDate(type: string): boolean
doesQuestionTypeHaveOptions(type: string): boolean
```

## Extensibility — Custom Configuration

Use `TemplatesCustomEntitiesService` to customize behavior:

```typescript
interface CustomConfiguration {
  customQuestionValidation?: (question: IQuestion) => string[];
  customQuestionValidationErrors?: { [key: string]: string };
  isQuestionALibraryQuestion?: (question: IQuestion) => boolean;
}
```

This allows extending validation rules, error messages, and library question detection.

## EditTemplateComponent

Embeddable component for editing templates:

```typescript
@Component({
  selector: 'template-builder-edit-template',
  template: '...'
})
export class EditTemplateComponent {
  @Input() templateInput: ITemplate;
  @Input() templateInputAsync: Observable<ITemplate>;
  @Input() getTemplateFromStore: boolean;
  @Input() hasSections: boolean = true;
  @Input() showDateRangePicker: boolean = false;
  @Input() showTextAreaMaxLenInputBox: boolean = false;
  @Input() resetIdGeneratorOnStart: boolean = true;
  @Input() clearStoreOnDestroy: boolean = true;
}
```

**Usage:**
```html
<template-builder-edit-template 
  [templateInput]="template"
  [hasSections]="true"
  [showDateRangePicker]="true">
</template-builder-edit-template>
```

Or with async template:
```html
<template-builder-edit-template 
  [templateInputAsync]="template$"
  [getTemplateFromStore]="true">
</template-builder-edit-template>
```
