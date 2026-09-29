---
name: uifw-header
description: 'Build application headers and navigation using the Frost topbar and left menu (@intersystems/header). USE FOR: app headers, navigation bars, desktop left menus, responsive mobile nav, topbar customization. DO NOT USE FOR: page content headers, section titles, breadcrumbs.'
argument-hint: 'Describe the header/navigation you need (sections, menu items, responsive behavior)'
---

# Header — Application Navigation

`@intersystems/header` provides `TopbarComponent` (responsive header) and `LeftMenuComponent` (desktop sidebar). Both are configurable and support custom content.

## Quick Start

### Install

```bash
npm install @intersystems/header --registry <artifactory url>
```

**app.module.ts:**
```typescript
import { HeaderModule } from "@intersystems/header";
@NgModule({ imports: [HeaderModule, ...] })
```

**angular.json** (add assets):
```json
{
  "assets": [{
    "glob": "**/*",
    "input": "node_modules/@intersystems/header/assets/images",
    "output": "./assets/images"
  }]
}
```

**styles.scss:**
```scss
@use "@intersystems/header/assets/styles/header-styles";
```

### Basic Topbar

```html
<fr-topbar [appNameConfig]="appNameData" [headerConfig]="headerData" [menusConfig]="menuData">
  <!-- All application content -->
</fr-topbar>
```

**CRITICAL:** Place ALL content inside `<fr-topbar>` for proper hamburger menu overlay.

```typescript
appNameData: AppNameConfig = { appName: 'My App', appNameAllCaps: true };
headerData: HeaderConfig = { companyLogo: { src: './assets/images/logo.svg' } };
menuData: MenusConfig = { onItemClick: (item) => this.handleClick(item) };
```

### Left Menu

```html
<fr-left-menu [config]="leftMenuConfig"></fr-left-menu>
```

```typescript
leftMenuConfig: LeftMenuConfig = {
  expanded: true,
  data: [
    { id: 'home', name: 'Home', iconName: 'home', route: '/home' },
    { id: 'settings', name: 'Settings', iconName: 'settings', route: '/settings' }
  ],
  onItemClick: (node) => this.router.navigate([node.route])
};
```

## Component API

### TopbarComponent Inputs

| Input | Type | Description |
|-------|------|-------------|
| `appNameConfig` | `AppNameConfig` | App name, display options, tab title |
| `headerConfig` | `HeaderConfig` | Company logo, back button, dark mode |
| `menusConfig` | `MenusConfig` | Hamburger, avatar, language, dropdown |

### LeftMenuComponent Input

| Input | Type | Description |
|-------|------|-------------|
| `config` | `LeftMenuConfig` | Tree data, expand/collapse, search, handlers |

## Features

| Feature | Reference |
|---------|-----------|
| Topbar configuration (logos, sections, responsive) | [topbar.md](references/topbar.md) |
| Left menu navigation (tree, icons, active state) | [left-menu.md](references/left-menu.md) |
| Dynamic updates via TopbarControlService | [topbar.md](references/topbar.md) |
| Custom content injection | [topbar.md](references/topbar.md) |

## Key Imports

```typescript
import { HeaderModule, TopbarComponent, LeftMenuComponent, 
         TopbarControlService, AppNameConfig, HeaderConfig, 
         MenusConfig, LeftMenuConfig } from "@intersystems/header";
```

## Dynamic Updates

```typescript
constructor(private topbarControl: TopbarControlService) {}

// Set page title
this.topbarControl.setPageTitle('Dashboard');

// Update username
this.topbarControl.setUsernameData({ userName: 'John Doe', textAboveUserName: 'Signed in as' });

// Change language
this.topbarControl.setLanguage({ id: 'fr-fr', name: 'French' });
```
