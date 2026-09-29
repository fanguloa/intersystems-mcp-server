---
name: uifw-stepper
description: 'Build multi-step wizards using @intersystems/stepper (Angular Material mat-stepper with Frost styling). USE FOR: multi-step workflows, guided wizards, sequential form completion, step-by-step processes. DO NOT USE FOR: tab navigation, single-page forms, progress indicators.'
argument-hint: 'Describe the wizard you need (steps, components per step, navigation behavior)'
---

# @intersystems/stepper

Build multi-step wizard workflows with custom components per step, using Angular Material's mat-stepper with Frost styling.

## Quick Start

### 1. Import StepperModule

```typescript
import { StepperModule } from "@intersystems/stepper";

@NgModule({
  imports: [
    StepperModule,
    // ... other imports
  ]
})
```

### 2. Define StepperConfig

```typescript
import { StepperConfig, AbstractStepperComponent } from "@intersystems/stepper";

// Create your step components (must extend AbstractStepperComponent)
export class FirstStepComponent extends AbstractStepperComponent {
  tabControl: FormControl; // Injected by stepper
  
  ngOnInit() {
    // Set tabControl.setValue(true) when step is complete
    this.tabControl.setValue(true);
  }
}

// In your parent component
stepperConfig: StepperConfig = {
  tabs: [
    { name: "First Step", component: FirstStepComponent },
    { name: "Second Step", component: SecondStepComponent },
    { name: "Final Step", component: FinalStepComponent }
  ]
};
```

### 3. Add Template

```html
<fr-stepper [config]="stepperConfig" [linear]="true"></fr-stepper>
```

## StepperComponent API

**Selector:** `<fr-stepper>`

**Inputs:**
- `config: StepperConfig` — Configuration object with tabs array
- `linear: boolean` — When true, enforces sequential navigation (user cannot skip steps)

**Built-in Navigation:**
- Automatic Next/Back buttons rendered for each step
- Next button disabled until step's `tabControl` is valid (when `linear=true`)
- Header navigation reflects step completion state

## StepperConfig Structure

```typescript
interface StepperConfig {
  tabs: StepperTab[];
}

interface StepperTab {
  component: AbstractStepperComponent;  // Component to render
  name: string;                         // Tab label (translatable)
}
```

## Step Component Pattern

Each step component must:

1. **Extend AbstractStepperComponent** (optional but recommended for type safety)
2. **Accept tabControl injection:**
   ```typescript
   tabControl: FormControl; // Automatically injected by stepper
   ```
3. **Signal completion** by setting `tabControl` value to `true`:
   ```typescript
   this.tabControl.setValue(true);  // Enables Next button
   ```

## Step Navigation Behavior

- **Linear mode (`linear=true`)**: Next button disabled until current step sets `tabControl` to true; header navigation restricted to completed steps + next step
- **Non-linear mode (`linear=false`)**: All Next buttons enabled; all header tabs clickable
- **Automatic controls**: Back button hidden on first step, Next button hidden on last step

## Key Imports

```typescript
import { StepperModule } from "@intersystems/stepper";
import { StepperConfig, StepperTab, AbstractStepperComponent } from "@intersystems/stepper";
```
