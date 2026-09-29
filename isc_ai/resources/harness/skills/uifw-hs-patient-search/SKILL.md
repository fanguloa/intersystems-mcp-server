---
name: uifw-hs-patient-search
description: 'Search and select patients using @healthshare/patient-search. USE FOR: patient context selection, searching patients by demographics, modal-based patient lookup, patient search results display. DO NOT USE FOR: displaying patient info (use uifw-hs-patient-banner), general search UIs, data filtering.'
argument-hint: 'Describe the patient search you need (modal vs inline, search fields, selection behavior)'
---

# @healthshare/patient-search

Angular component for patient search and selection with modal and inline modes.

## Installation

```bash
npm install @healthshare/patient-search --registry https://arti.iscinternal.com/api/npm/npm/
```

Import module:
```typescript
import { PatientSearchModule } from "@healthshare/patient-search";

@NgModule({
  imports: [PatientSearchModule, ...]
})
```

Import styles in `styles.scss`:
```scss
@use "@healthshare/patient-search/src/assets/styles/hs-patient-search-modal-panel.scss";
```

## Quick Start: Modal Search

```typescript
import { MatDialog } from "@angular/material/dialog";
import { PatientSearchModalComponent, PatientSearchConfig } from "@healthshare/patient-search";

constructor(private dialog: MatDialog) {}

openPatientSearch() {
  const config: PatientSearchConfig = {
    SearchFDN: this.buildSearchFormFDN(),
    GeneralConfiguration: this.getGeneralConfig(),
    ResultsColumnConfiguration: this.getColumnConfig(),
    onSearchInvoked: (formModel) => this.performSearch(formModel),
    onPatientSelected: (patient) => this.handleSelection(patient),
    searchResult: this.searchResults$, // Observable<PatientSearchResult>
    selectOptionObservables: {} // async dropdown options
  };

  const dialogRef = this.dialog.open(PatientSearchModalComponent, {
    id: "patient-search-dialog",
    data: config,
    panelClass: "fr-layout-wrapper-mat-dialog-panel"
  });
}

handleSelection(patient: PatientResultRow) {
  console.log('Selected patient:', patient.mpiid, patient.matches);
}
```

## PatientSearchConfig Interface

```typescript
interface PatientSearchConfig {
  SearchFDN: FDN;                                    // isc-form definition for search fields
  GeneralConfiguration: GeneralConfiguration;        // messages, fonts, icons
  ResultsColumnConfiguration: {
    ColumnsLarge: ResultColumn[];
    ColumnsMedium: ResultColumn[];
    ColumnsSmall: ResultColumn[];
  };
  onSearchInvoked: (formModel: any) => void;        // callback when search submitted
  onPatientSelected: (patient: PatientResultRow) => void;
  searchResult: Observable<PatientSearchResult>;     // emits search results
  selectOptionObservables: Record<string, Observable<Option[]>>;
  
  // Optional consent override features
  userCanOverrideDataRestrictionsFromSearch?: boolean;
  userCanOverrideDataRestrictionsFromSearchResults?: boolean;
  overrideDataModalTitle?: string;
  disableCloseModalOnRowClick?: boolean;            // keep modal open after selection
  disableMRNMatches?: boolean;                      // disable expandable MRN rows
}
```

## Inline Component Usage

```html
<hs-patient-search
  [data]="patientSearchConfig"
  (patientSelected)="onPatientSelected()">
</hs-patient-search>
```

## Integration with Patient Banner

After patient selection, use `@healthshare/patient-banner` to display patient context:

```typescript
import { PatientBannerComponent } from "@healthshare/patient-banner";

onPatientSelected(patient: PatientResultRow) {
  // Pass patient.mpiid to patient banner component
  this.selectedPatientMPIID = patient.mpiid;
}
```

## Key Imports

```typescript
import { PatientSearchModule } from "@healthshare/patient-search";
import { PatientSearchModalComponent } from "@healthshare/patient-search";
import { PatientSearchComponent } from "@healthshare/patient-search";
import { PatientSearchConfig, ResultColumn } from "@healthshare/patient-search";
import { PatientResultRow, PatientResultRowMatch } from "@healthshare/patient-search";
```
