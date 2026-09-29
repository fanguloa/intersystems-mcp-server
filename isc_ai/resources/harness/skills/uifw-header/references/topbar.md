# Topbar Configuration

## AppNameConfig
```typescript
interface AppNameConfig {
  appName?: string;                    // App name in header
  appNameAllCaps?: boolean;            // Default: true
  useOnMobile?: boolean;               // Show on mobile. Default: true
  showAppNameInTab?: boolean;          // Include in tab title. Default: true
  showPageTitleInHeader?: boolean;     // Show page title. Default: true
}
```

## HeaderConfig
```typescript
interface HeaderConfig {
  companyLogo?: CompanyLogoConfig;     // Left logo
  showSpacerOnMobile?: boolean;        // Pin avatar right. Default: false
  useBackButton?: boolean;             // Show back button. Default: false
  onBackButtonClick?: (event) => void; // Back callback
  useDivider?: boolean;                // Divider after hamburger. Default: true
  includeDarkMode?: boolean;           // Dark mode toggle. Default: false
}

interface CompanyLogoConfig {
  src?: string | SafeUrl;              // Logo path
  darkModeSrc?: string | SafeUrl;      // Dark mode logo
  link?: string;                       // routerLink
  tooltip?: string; altText?: string;
  useCompanyLogo?: boolean;            // Show logo. Default: true
  hideOnMobile?: boolean;              // Hide on mobile. Default: true
}
```

## MenusConfig
```typescript
interface MenusConfig {
  hamburgerMenu?: HamburgerMenuConfig;
  avatarMenu?: AvatarMenuConfig;
  languageMenu?: LanguageConfig;
  dropdown?: DropdownConfig;
  productLogo?: LogoConfig;
  onItemClick: (item: DropdownData | FlatNode) => void;  // Required
}
```

## Hamburger Menu
```typescript
interface HamburgerMenuConfig {
  useOnDesktop?: boolean;              // Default: false
  useOnMobile?: boolean;               // Default: false
  data?: AppNode[];                    // Tree data
  customHamburgerMenuContent?: Type<any>;  // Custom component
}
```

**Custom content:** Component receives `menusConfig`, `includeDropdownData`, `sideNavRef` inputs.
```html
<fr-topbar><custom-menu fr-header-insertable-content></custom-menu></fr-topbar>
```

## Avatar Menu
```typescript
interface AvatarMenuConfig {
  useOnMobile?: boolean;               // Default: false
  showUserTooltip?: boolean;           // Default: true
  onUsernameClick?: (event) => void;   // Override default dropdown
}
```
Set via `TopbarControlService.setUsernameData()`.

## Language Selection
```typescript
interface LanguageConfig {
  languageSelectSupported?: boolean;   // Enable selector. Default: false
  languages?: LanguageData[];          // Available languages
  initialLanguage?: LanguageData;      // Starting language
  showNameOnDesktop?: boolean;         // Full name. Default: true
  showLangCodeOnMobile?: boolean;      // Code. Default: true
  updateGlobalLangAttribute?: boolean; // Update <html lang>. Default: true
}
interface LanguageData { id: string; name: string; }  // id: ISO code (e.g., 'en-us')
```

## Utility Dropdown
```typescript
interface DropdownConfig { data?: DropdownData[] | Observable<DropdownData[]>; }
interface DropdownData { id: string; name: string; disabled?: boolean; }
```

## TopbarControlService
```typescript
constructor(private topbarControl: TopbarControlService) {}

this.topbarControl.setPageTitle('Dashboard');
this.topbarControl.setUsernameData({ userName: 'Jane', textAboveUserName: 'Welcome', hideOnMobile: false });
this.topbarControl.setAlternateAvatarIcon({ text: 'JS', class: 'custom-class' });
this.topbarControl.setLanguage({ id: 'fr-fr', name: 'French' });
```

## Custom Content Projection
Inject left of language/utility dropdowns:
```html
<fr-topbar><my-component fr-header-insertable-content></my-component></fr-topbar>
```

## Responsive
- Desktop (>= 600px): Full header + optional left menu
- Mobile (< 600px): Compact, hamburger recommended
- Company logo/avatar hidden on mobile by default
