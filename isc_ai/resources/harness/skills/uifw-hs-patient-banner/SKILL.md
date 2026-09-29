---
name: uifw-hs-patient-banner
description: 'Display patient context information using @healthshare/patient-banner. USE FOR: patient demographic headers, compact patient banners, override consent modals, patient selection from list. DO NOT USE FOR: patient search (use uifw-hs-patient-search), clinical data display, form fields.'
argument-hint: 'Describe the patient banner you need (full/compact, override capability, data fields)'
---

# @healthshare/patient-banner

Display patient demographic and context information in HealthShare applications using the patient banner component.

## Quick Start

Install the package:
```bash
npm install @healthshare/patient-banner --registry https://arti.iscinternal.com/api/npm/npm/
```

Import the module in your `app.module.ts`:
```typescript
import { PatientBannerModule } from "@healthshare/patient-banner";

@NgModule({
  imports: [PatientBannerModule]
})
```

Basic usage:
```typescript
<hs-patient-banner
  [patientBannerData]="patientBannerData"
  [showOverrideConsentButton]="true"
  [isPatientDeceased]="false"
  [overrideConsentApplied]="false"
  (overrideDataEvent)="handleOverrideRequest($event)"
></hs-patient-banner>
```

## PatientBannerComponent API

### Inputs
- **`patientBannerData: BannerData`** - Patient information and configuration (see data structure below)
- **`showOverrideConsentButton: boolean`** - Whether to show the "Override Consent" button (default: `false`)
- **`isPatientDeceased: boolean`** - Apply deceased styling (gray accent color) (default: `false`)
- **`overrideConsentApplied: boolean`** - Show override applied state (default: `false`)

### Outputs
- **`overrideDataEvent: EventEmitter<OverrideDataEvent>`** - Emitted when user clicks "Override Consent" button

### BannerData Structure
```typescript
{
  PatientInformation: {
    NameBlock: { LastName: { Value: "Smith" }, FirstName: { Value: "John" } },
    DemographicsBlock: { Gender: { Label: "Gender", Value: "Male" }, Age: { Label: "Age", Value: "45" } },
    ContactInfoBlock: { Street: { Label: "Address", Value: "123 Main St" }, City: { Value: "Boston" } }
  },
  GeneralConfiguration: {
    Fonts: { BannerText: { Size: "18px", Weight: "bold" } },
    Colors: { AccentColor: "#008f8a" },
    ConsentMessages: { ConsentDataIsHiddenText: "Some patient data is restricted" }
  }
}
```

## Additional Components

See [references/banner-variants.md](references/banner-variants.md) for:
- **SmallPatientBannerComponent** - Compact banner for modals
- **OverrideDataComponent** - Consent override modal
- **PatientSelectionComponent** - Multi-patient selection menu
- **PatientBannerConfigService** - Translation configuration

## Key Imports

```typescript
import { 
  PatientBannerModule,
  PatientBannerComponent,
  SmallPatientBannerComponent,
  OverrideDataComponent,
  PatientSelectionComponent,
  PatientBannerConfigService,
  BannerData,
  OverrideDataEvent,
  OverrideDialogData,
  OverrideForm
} from "@healthshare/patient-banner";
```
