# ngrx Setup — State Management for Template Builder

## Store Registration

The library exports pre-configured reducer and metaReducers. Register using `StoreModule.forFeature`:

```typescript
import { 
  BuilderModule, 
  builderReducer, 
  metaReducers, 
  TEMPLATE_BUILDER_STATE_KEY 
} from '@intersystems/template-builder';

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

**State key:** `ISC_TEMPLATE_BUILDER`

## State Structure

```typescript
interface BuilderState {
  template: ITemplateState;
}

interface ITemplateState {
  activeTemplate: ITemplate;                    // Raw template
  activeTemplateNormalized: normalizedData;     // Normalized for editing
  activeTemplateDeletedQuestions: { ... };      // Deleted questions cache
  allTemplates: ITemplateListItem[];            // Template list
  templateCounts: any;
  temporaryIdToRealIdMap?: { [key: string]: any };
  xslTemplate: string;
}

interface normalizedData {
  entities: {
    question?: { [id: string]: IQuestion };
    section?: { [id: string]: ISection };
  };
  result: any[];  // Array of top-level IDs (sections or questions)
}
```

Template data is normalized using `normalizr` for efficient nested updates. Sections and questions are stored by ID in `entities`, with ordering preserved in `result`.

## Core Actions

### Question Actions
```typescript
UpdateQuestionById({ question: IQuestion })
SwapQuestions({ sectionId: ID_TYPE, questionIdx1: number, questionIdx2: number })
AddQuestion({ question: IQuestion })
```

### Section Actions
```typescript
UpdateSectionById({ section: ISection })
SwapSections({ sectionIdx1: number, sectionIdx2: number })
AddSection({ section: ISection, question: IQuestion })
```

### Template Actions
```typescript
UpdateUIModel({ UI_MODEL: TemplateUIModel })
GetTemplate({ Id: string })
GetTemplateSuccess({ template: ITemplate })
GetTemplateSuccessCacheDeletedQuestions({ deletedQuestions: any })
resetTemplate()
UpdateNormalizedTemplate({ normalizedTemplate: normalizedData })
UpdateTitle({ templateTitle: string })
UpdateSummary({ summary: string })
UpdateXSL({ xslTemplate: string })
UpdateCustomPDFFooterForTemplate({ customPDFFooterForTemplate: string })
UpdateDocumentType({ documentTypeID: string })
```

### Logic Management Actions
```typescript
PlaceLibraryQuestionIntoTemplate({ 
  overwrittenQuestion: IQuestion, 
  overwrittenQuestionIdx: number,
  isQuestionTemporary: boolean,
  libraryQuestion: IQuestion 
})
RemoveDeletedEntityFromTrigger({ 
  entityType: string, 
  entityId: ID_TYPE, 
  deletedEntity: any, 
  from: string 
})
RemoveLogicOnEntityDeleted({ deletedEntityType: string, deletedEntity: any })
RemoveLogicOnQuestionTypeChanged({ entity: any })
UpdateLogicOnOptionEdited({ 
  entityType: string, 
  entityId: string, 
  updatedQuestionIdentifier: string,
  oldOption: any, 
  newOption: any 
})
```

## Selectors

```typescript
// Raw template
selectActiveTemplate(state: AppState): ITemplate

// Normalized state
selectActiveTemplateNormalized(state: AppState): normalizedData

// Top-level IDs (sections or questions)
selectTopLevelNormalizedIDs(state: AppState): any[]

// Deleted questions cache
selectActiveTemplateDeletedQuestions(state: AppState): IActiveTemplateDeletedQuestions

// Question by ID
selectQuestionById({ questionId: ID_TYPE })(state: AppState): IQuestion

// Section by ID
selectSectionById({ sectionId: ID_TYPE })(state: AppState): ISection

// Section logic check
doesSectionMemberQuestionsHaveLogic({ 
  sectionId: string, 
  utilities: { ... } 
})(state: AppState): boolean
```

## metaReducers

The library exports one metaReducer:

```typescript
export const metaReducers: MetaReducer<any>[] = [clearState];
```

**clearState:** Resets entire state to `initialBuilderState` when `CLEAR_STATE` action is dispatched. Used for cleanup on navigation or logout.

```typescript
export const ClearStateAction = createAction('CLEAR_STATE');
```

## Extending the Reducer

You can combine the built-in reducer actions with custom actions:

```typescript
import { 
  iscTemplateReducerOns,
  resetTemplate as IscResetTemplate
} from '@intersystems/template-builder';

export const templateReducer = createReducer(
  initialTemplateState,
  ...iscTemplateReducerOns,  // All OOTB reducer actions
  // Override built-in action
  on(IscResetTemplate, (state, payload) => ({
    ...customResetLogic(state, payload)
  })),
  // Add custom action
  on(YourCustomAction, (state, payload) => ({
    ...customLogic(state, payload)
  }))
);
```

Then register your custom reducer instead of the default:

```typescript
@NgModule({
  imports: [
    StoreModule.forFeature(TEMPLATE_BUILDER_STATE_KEY, 
      { template: templateReducer }, 
      { metaReducers: metaReducers }
    )
  ]
})
```
