---
name: uifw-hs-isc-form
description: 'HealthShare-specific form field extensions using @healthshare/isc-form. USE FOR: clinical data display wrappers (read-only patient data), patient question fields with clinician comments, HealthShare-specific form customization. DO NOT USE FOR: general form building (use uifw-isc-form), standalone clinical dashboards, patient banners.'
argument-hint: 'Describe the clinical form field you need (clinical data wrapper, patient question)'
---

# @healthshare/isc-form

HealthShare-specific extensions for `@intersystems/isc-form` that provide clinical data wrappers and patient-clinician interaction patterns.

## Quick Start

### Installation

```bash
npm install @healthshare/isc-form --registry <artifactory-url>
```

```typescript
import { HsIscFormModule } from "@healthshare/isc-form";

@NgModule({
  imports: [HsIscFormModule]
})
```

## Components

### ClinicalFieldComponent

**Wrapper:** `FIELD_TYPES.CLINICAL` (or `"clinical"`)

Displays read-only patient clinical data (medications, allergies) pulled from SDA sources. Users can select which clinical records to include in the form but cannot edit the clinical data itself.

**Field Type:** Works only with `checkboxTable` fields.

**Basic Usage:**
```typescript
{
  key: "patient-medications",
  type: "checkboxTable",
  wrappers: [FIELD_TYPES.CLINICAL],
  clinicalBehavior: {
    editFieldButtonText: "Select Patient Data",
    patientIdentifierType: "CareCommunity",
    patientIdentifierKey: "additionalModels.patientId",
    dataSource: "eastborough.iscinternal.com:HSACCESS",
    dataType: "SDA",
    dataPath: "medication",
    noKnownCheckboxLabel: "No medications relevant",
    noKnownRowData: { medication_name: "No medications relevant" }
  }
}
```

**FormConfig (for edit mode):**
```typescript
{
  getPatientDataForClinicalField: (field: Field, formState: FormState): Observable<any> => {
    return this.apiService.get({
      url: `patient/${get(formState, field.clinicalBehavior.patientIdentifierKey)}/${field.clinicalBehavior.dataPath}`
    });
  }
}
```

### PatientQuestionComponent

**Wrapper:** `"patient-question"`

Wraps questions meant for patient responses. Clinicians cannot answer directly but can add comments visible to the patient. Patients see comments but cannot reply.

**Field Type:** Works with any field type.

**Basic Usage:**
```typescript
{
  key: "patient-is-smoking",
  type: "radio",
  wrappers: ["patient-question"],
  // Comments stored at key: "patient-is-smoking_Comments"
}
```

**FormConfig (for clinician commenting):**
```typescript
{
  patientQuestionConfig: {
    canComment: true,  // resource permission
    currentUser: this.currentUserModelService.getCurrentUser(),
    getNewComment: (): Observable<FormComment> => {
      return this.cmAPIService.post({ url: "api/comments" }, {});
    }
  }
}
```

## References

- [clinical-field.md](references/clinical-field.md) - Detailed clinical wrapper configuration and patient data integration
