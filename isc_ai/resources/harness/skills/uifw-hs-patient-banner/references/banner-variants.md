# Patient Banner Variants

## SmallPatientBannerComponent

Compact patient banner for use in modals and constrained spaces.

### Usage
```typescript
<hs-small-patient-banner
  [name]="nameBlock"
  [demographics]="demographicsBlock"
></hs-small-patient-banner>
```

### Inputs
```typescript
name: NameBlockInput = { lastName: "Smith", firstName: "John", middleName: "Robert" };
demographics: DemographicsBlockInput = { gender: "Male", age: "45", birthDateTime: "1979-03-15" };
```

## OverrideDataComponent

Modal dialog for overriding patient consent restrictions.

### Usage
```typescript
handleOverrideRequest(event: OverrideDataEvent) {
  const dialogData: OverrideDialogData = {
    showPatientBanner: true,
    nameBlock: event.nameBlock,
    demographicsBlock: event.demographicsBlock,
    messages: ["This patient has restricted data."],
    modalTitle: "Override Patient Consent",
    form: {
      overrideButtonText: "Submit Override",
      authType: { label: "Authorization Type", required: true,
        options: [{ name: "Emergency", value: "emergency" }] },
      fields: {
        authorizedBy: { label: "Authorized By", hidden: false, required: true },
        reasonForOverride: { label: "Reason", hidden: false, required: true },
        roleOfAuthorizingIndividual: { label: "Role", hidden: false, required: false }
      }
    }
  };

  this.dialog.open(OverrideDataComponent, {
    data: dialogData, panelClass: "fr-layout-wrapper-mat-dialog-panel"
  }).afterClosed().subscribe((response: OverrideForm) => {
    if (response) console.log("Override:", response);
  });
}
```

## PatientSelectionComponent

Menu for selecting between multiple patient contexts.

### Usage
```typescript
<hs-patient-selection
  [patientSelectionMenuConfig]="selectionConfig"
></hs-patient-selection>
```

### Configuration
```typescript
import { PatientSelectionConfig } from "@healthshare/patient-banner";

selectionConfig: PatientSelectionConfig = {
  patientSelectionButtonText: "Select Patient",
  applications: [
    { name: "Emergency Department", id: "ed-app", disabled: false },
    { name: "Radiology", id: "rad-app", disabled: false },
    { name: "Lab Results", id: "lab-app", disabled: true }
  ],
  onApplicationClick: (application) => {
    console.log("Selected application:", application.name);
    // Navigate to the selected application context
  }
};
```

## PatientBannerConfigService

Service for configuring translations in the patient banner library.

### Setup
```typescript
constructor(
  private translate: TranslateService,
  private patientBannerConfigService: PatientBannerConfigService
) {
  this.translate.onLangChange.subscribe(() => {
    this.patientBannerConfigService.configureTranslation();
  });
  translate.use("en-us");
}
```

### Supported Locales
The service includes translations for: `af-za`, `ar-ar`, `cs-cz`, `da-dk`, `de-de`, `en-au`, `en-gb`, `en-us`, `es-cl`, `es-es`, `fi-fi`, `fr-fr`, `it-it`, `ja-jp`, `nl-nl`, `pt-br`, `sv-se`, `xh-za`, `zh-cn`, `zu-za`.
