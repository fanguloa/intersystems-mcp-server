---
name: uifw
description: 'Use when designing, brainstorming, or planning Angular UI features to discover which @intersystems/UIFW components exist and their capabilities. Also use for: UIFW app setup, importing styles, theming/dark mode, responsive breakpoints, CSS variables, SCSS mixins, upgrading. DO NOT USE FOR: specific component API details (use the component-specific skill instead, e.g. uifw-table, uifw-isc-form).'
argument-hint: 'Describe what you need help with (app setup, theming, choosing a component, designing a feature)'
---

# UIFW Master Skill

This skill is the entry point for all UIFW (UI Framework) work. It helps you choose the right component library, configure the Frost design system, and set up theming/styling.

## Design-Phase Usage

**Invoke this skill during brainstorming or plan-writing** when the feature involves Angular UI. The Library Selection Guide below tells you which component libraries exist and what they do — this directly informs architectural decisions (e.g., whether to use isc-form vs a custom reactive form, whether isc-table supports the needed interaction pattern).

**When writing implementation plans:** Each task that uses a UIFW component MUST include a step telling the implementing agent to invoke the relevant component skill (e.g., `uifw-table`, `uifw-isc-form`). The implementing agent starts with zero context — without the skill invocation step, it will guess at APIs instead of using the documented patterns.

## Getting Started

### Installation

Install the core styles library (required by all UIFW components):

```bash
npm install @intersystems/styles --registry <artifactory url>
```

### angular.json Configuration

Add stylesheet preprocessor paths to `angular.json` under `architect.build.options`:

```json
"stylePreprocessorOptions": {
  "includePaths": ["./node_modules"]
}
```

### Import Core Styles

In your application's root `styles.scss`, import the Frost core styles:

```scss
@use '@intersystems/styles/css/core-styles';
```

### Peer Dependencies

The styles library requires:
- `@angular/common` ^18.2.9
- `@angular/core` ^18.2.9
- `@angular/material` ^18.2.9

## Library Selection Guide

When a user asks "how do I add X to my app?", use this table to route to the correct component-specific skill:

| Use Case | Skill Name |
|----------|-----------|
| Display tabular data | `uifw-table` |
| Collect user input via forms | `uifw-isc-form` |
| Show loading state | `uifw-spinner` |
| App header/navigation | `uifw-header` |
| Show notifications/toasts | `uifw-notification` |
| Confirm destructive actions | `uifw-confirmation-dialog` |
| Show version/about info | `uifw-about-dialog` |
| Token-based IRIS auth | `uifw-auth` |
| OAuth/OIDC auth | `uifw-hs-auth` |
| Session timeout | `uifw-timeout` |
| Error pages (404/403) | `uifw-error-pages` |
| Data visualization/charts | `uifw-chart` |
| Hierarchical column browsing | `uifw-column-view` |
| Resizable grid layouts | `uifw-grid-layout` |
| Text comparison/diff | `uifw-text-diff` |
| Multi-step wizards | `uifw-stepper` |
| Advanced data filtering | `uifw-filter-widget` |
| ISC icon set | `uifw-icons` |
| Utility pipes (date, safe URL) | `uifw-shared-utilities` |
| Application selector modal | `uifw-user-default` |
| Source control sessions | `uifw-change-control` |
| Change history logs | `uifw-change-history` |
| In-place element customization | `uifw-customizable-element` |
| Template/form builder | `uifw-template-builder` |
| Question library for templates | `uifw-template-question-library` |
| Shared type definitions | `uifw-types` |
| CDS Hooks cards | `uifw-cds-card` |
| Patient banner | `uifw-hs-patient-banner` |
| Patient search | `uifw-hs-patient-search` |
| HS login pages | `uifw-hs-basic-login` / `uifw-hs-custom-login` |
| HS clinical form fields | `uifw-hs-isc-form` |

## Frost Design System

The Frost design system provides a comprehensive set of CSS variables and design tokens. All CSS variables are defined in the core-styles and should be used directly in your application.

### Color Variables

Use these CSS variables for colors (override only with UX approval):

- **Primary Colors**: `--sys-primary`, `--sys-secondary`, `--sys-tertiary`
- **Surface Colors**: `--sys-background`, `--sys-surface`, `--sys-surface-variant`, `--sys-outline`, `--sys-shadow`
- **State Colors**: `--sys-state-new`, `--sys-state-severe`, `--sys-state-selected`, `--sys-state-hover`, `--sys-state-linked`, `--sys-state-utility`, `--sys-state-focused`, `--sys-state-disabled`
- **Gray Scale**: `--sys-near-white`, `--sys-very-light-grey`, `--sys-light-grey`, `--sys-middle-grey`, `--sys-middle-dark-grey`, `--sys-dark-grey`, `--sys-very-dark-grey`, `--sys-on-background`
- **Visualization Colors**: `--sys-visualization-one` through `--sys-visualization-eight`, `--sys-visualization-positive`, `--sys-visualization-negative`
- **Alert Colors**: `--sys-alert-informational-*`, `--sys-alert-success-*`, `--sys-alert-warning-*`, `--sys-alert-error-*` (each with `-border`, `-icon`, `-fill`)

### Button Styling

Angular Material buttons with Frost classes:

- **Primary**: `<button mat-flat-button color="primary" class="fr-primary">Label</button>`
- **Secondary**: `<button mat-stroked-button color="primary" class="fr-secondary">Label</button>`
- **Tertiary**: `<button mat-stroked-button color="primary" class="fr-tertiary">Label</button>`
- **Icon**: `<button mat-icon-button class="fr-icon-button"><mat-icon>icon_name</mat-icon></button>`

## Theming

The ThemingService provides runtime CSS variable overrides and dark mode support.

See [references/theming.md](./references/theming.md) for detailed API documentation.

## Responsive Design

The FrBreakpointObserver provides reactive breakpoint detection.

See [references/breakpoints.md](./references/breakpoints.md) for detailed API documentation.

## Upgrading

The `@intersystems/schematics` package provides upgrade scripts and tooling.

See [references/schematics.md](./references/schematics.md) for detailed upgrade guidance.

## Cross-Cutting Patterns

### ConfigService Pattern

Most UIFW libraries expose a `*ConfigService` (e.g., `HeaderConfigService`, `TableConfigService`) for library-wide configuration. Inject these services in your app root and configure once at startup.

### Localization

UIFW libraries use `@ngx-translate` for internationalization. Configure `TranslateModule` in your app root and provide translation files for all supported locales.

### Standalone vs NgModule

UIFW libraries support both standalone components and NgModule-based apps. For standalone, import components directly. For NgModule, import the library's `*Module` (e.g., `HeaderModule`, `TableModule`).

## Layout Wrapper

For Frost-compliant page layouts, import the layout wrapper:

```scss
@use "@intersystems/styles/css/layout-wrapper";
```

For modals using MatDialog, also add to global `styles.scss`:

```scss
@use "@intersystems/styles/css/layout-wrapper-mat-dialog-panel";
```

Then configure the panel class:

```typescript
this.dialog.open(MyComponent, {
  panelClass: ["fr-layout-wrapper-mat-dialog-panel"]
});
```

## SCSS Mixins

To use Frost mixins, import them in component stylesheets:

```scss
@use "@intersystems/styles/css/mixins";

.my-icon {
  @include mixins.svg-or-icon(20px, var(--sys-primary));
  &:focus {
    @include mixins.focus-outline(3px);
  }
}
```

## Material Icons

UIFW includes the Material Icons font. See the [Confluence page](https://usconfluence.iscinternal.com/display/HSAF/Incorporating+Icons) for usage details.
