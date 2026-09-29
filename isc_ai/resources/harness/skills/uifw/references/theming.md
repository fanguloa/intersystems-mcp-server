# Theming Reference

The `ThemingService` provides runtime CSS variable overrides and dark mode support for UIFW applications.

## Import

```typescript
import { ThemingService } from "@intersystems/styles";
```

The service is provided at root level, so just inject it:

```typescript
constructor(private themeService: ThemingService) {}
```

## API

### setTheme(theme: ITheme): void

Sets CSS custom properties at the document root level.

```typescript
this.themeService.setTheme({
  "--sys-primary": "#ff5733",
  "--my-custom-variable": "#f2f2f2"
});
```

**ITheme interface:** `{ [key: string]: string }`

### getPropertyValue(propName: string): string

Retrieves the computed value of a CSS custom property.

```typescript
const primaryColor = this.themeService.getPropertyValue("--sys-primary");
```

### getAllCustomCSSProperties(): string[]

Returns all CSS custom property names defined in `:root` across all stylesheets.

```typescript
const allVars = this.themeService.getAllCustomCSSProperties();
// Returns: ["--sys-primary", "--sys-secondary", ...]
```

## Dark Mode

### darkMode: Signal<boolean>

Public readonly signal that emits the current dark mode state.

```typescript
import { effect } from "@angular/core";

private _onThemeChange = effect(() => {
  const isDark = this.themeService.darkMode();
  console.log("Dark mode is", isDark ? "enabled" : "disabled");
});
```

### toggleDarkMode(): void

Toggles dark mode on/off.

```typescript
this.themeService.toggleDarkMode();
```

### setDarkMode(isDark: boolean): void

Explicitly sets dark mode state.

```typescript
this.themeService.setDarkMode(true); // Enable dark mode
```

### enableDarkMode(includeDarkMode: boolean): void

Helper to initialize dark mode based on app configuration. If `includeDarkMode` is `false`, dark mode is disabled regardless of stored preferences. If `true`, uses stored preference or system preference.

```typescript
// In app initialization
this.themeService.enableDarkMode(environment.features.darkMode);
```

## Dark Mode Behavior

- Dark mode state is persisted to `localStorage` under key `isc-is-dark-mode`
- On initialization, reads from localStorage or falls back to system preference (`prefers-color-scheme: dark`)
- When enabled, adds the `dark` class to `document.documentElement`
- Compatible with Tailwind's `darkMode: ["class", ".dark"]` configuration

## Usage with @intersystems/header

The Header component provides a built-in dark mode toggle. Just enable dark mode in the header configuration:

```typescript
headerConfigService.config = {
  darkMode: true
};
```

The header will automatically use the ThemingService to toggle dark mode when the user clicks the theme icon.

## Overriding CSS Variables

Example: Customize form field colors

```typescript
this.themeService.setTheme({
  "--form-field-border": "#cccccc",
  "--form-field-focus": "#4a90e2"
});
```

**Important:** CSS variable overrides should only be used with UX approval. The Frost design system provides a comprehensive set of variables that cover most use cases.
