# Clinical Field Reference

## ClinicalFieldComponent

Pulls read-only patient data (SDA) into a form. Users select which records to include but cannot modify clinical data. Includes "none applicable" option when no data exists.

**Requirements:** `checkboxTable` field type, `FIELD_TYPES.CLINICAL` wrapper

### ClinicalBehavior Properties

```typescript
{
  editFieldButtonText: string;           // Modal button label
  patientIdentifierType: string;         // e.g., "CareCommunity"
  patientIdentifierKey: string;          // FormState path to patient ID
  dataSource: string;                    // SDA source endpoint
  dataType: "SDA";                       // Data type
  dataPath: "medication" | "allergy";    // SDA path
  noKnownCheckboxLabel: string;          // "None applicable" label
  noKnownRowData: Record<string, string>; // "None applicable" row data
}
```

### FormConfig (Edit Mode)

Enable edit mode with `getPatientDataForClinicalField`:

```typescript
formConfig: {
  getPatientDataForClinicalField: (field: Field, formState: FormState): Observable<any> => {
    const patientId = get(formState, field.clinicalBehavior.patientIdentifierKey);
    return this.apiService.get({ 
      url: `api/patient/${patientId}/${field.clinicalBehavior.dataPath}` 
    });
  }
}
```

Without this callback, the field renders in view-only mode.

## PatientQuestionComponent

Patient-facing questions with clinician comments. Patients answer; clinicians add/edit/delete comments. Patients view comments but cannot reply.

**Requirements:** Any field type, `"patient-question"` wrapper

**Comment Storage:** `{fieldKey}_Comments` (e.g., `"patient-is-smoking"` → `"patient-is-smoking_Comments"`)

### FormConfig (Clinician Comments)

```typescript
formConfig: {
  patientQuestionConfig: {
    canComment: boolean;                  // Resource permission
    currentUser: { username: string };    // For ownership checks
    getNewComment: (): Observable<FormComment> => {
      return this.commentAPI.post({ url: "/api/comments" }, {});
    }
  }
}

interface FormComment {
  Id: string;
  LastModifiedAuthor: string;
  LastModifiedAuthorUsername: string;
  LastModified: string;
  Text: string;
  action?: "" | "new" | "edit" | "delete";
}
```

**Workflow:** Clinician clicks "Add Comment" → enters text → Save marks `action: "edit"` and adds to `{fieldKey}_Comments` array. Clinicians can edit/delete their own comments; patients view only.
