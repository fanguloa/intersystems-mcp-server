---
name: uifw-shared-utilities
description: 'Use utility pipes and services from @intersystems/shared-utilities. USE FOR: fnCall pipe (calling functions in templates), provideDefault pipe, safeUrl/safeHtml pipes, Day.js date formatting, shared utility services. DO NOT USE FOR: component-specific utilities, auth services, form utilities.'
argument-hint: 'Describe which utility you need (pipe name, date formatting, safe content)'
---

# @intersystems/shared-utilities

Angular utility pipes and services for common template and data operations.

## Quick Start

```typescript
import { SharedUtilitiesModule } from '@intersystems/shared-utilities';

@NgModule({
  imports: [SharedUtilitiesModule]
})
```

Or import individual pipes/services for tree-shaking via sub-entry points:
```typescript
import { ApiServiceModule } from '@intersystems/shared-utilities/api';
import { SSOModule } from '@intersystems/shared-utilities/sso';
```

## Utility Pipes

### fnCall Pipe
Call functions directly in templates with arguments:

```html
<!-- Component -->
printArgsWithPipe = (arg1: string, arg2: string) => {
  this.doSomethingOnTheSide();
  return `${arg1} - ${arg2}`;
};

<!-- Template -->
<p>{{ "arg1" | fnCall: printArgsWithPipe:"arg2" }}</p>
```

**IMPORTANT**: Functions passed to fnCall **must** be arrow functions to preserve `this` context.

### provideDefault Pipe
Provide fallback values for nullish data:

```html
<p>{{ someUndefinedVar | provideDefault: "default text" }}</p>
<p>{{ user?.name | provideDefault: "Anonymous" }}</p>
```

Returns the default value if the input is falsy (null, undefined, "", 0, false).

### safeUrl Pipe
Bypass Angular's security and trust URL values:

```html
<iframe [src]="dynamicUrl | safeUrl"></iframe>
```

### safeHtml Pipe
Bypass security to render trusted HTML:

```html
<div [innerHTML]="userContent | safeHtml"></div>
```

### dayjs Pipe
Localized date formatting using Day.js with ngx-translate integration:

```html
<!-- Default format (LL) -->
<p>{{ dateValue | dayjs }}</p>

<!-- Custom format -->
<p>{{ dateValue | dayjs: 'YYYY-MM-DD' }}</p>

<!-- With time -->
<p>{{ dateValue | dayjs: 'LL': true }}</p>

<!-- Custom date and time formats -->
<p>{{ dateValue | dayjs: 'YYYY-MM-DD': true: 'HH:mm:ss' }}</p>

<!-- UTC mode -->
<p>{{ dateValue | dayjs: 'LL': false: null: true }}</p>
```

**Parameters**: `(dateFormat?, displayTime?, timeFormat?, useUtc?)`

**Special formats**:
- `LLWithShortMonth` - Localized long date with abbreviated month
- `TEXT` - Pass through string value unchanged

Returns Observable<string> that updates when language changes.

## DayJsPipeConfigService

Configure Buddhist Era calendar support:

```typescript
import { DayJSPipeConfigService } from '@intersystems/shared-utilities';

constructor(private dayJsPipeConfig: DayJSPipeConfigService) {
  // Enable Buddhist Era formatting
  this.dayJsPipeConfig.setUseBuddhistEra(true);
}
```

When enabled, 'Y' format tokens are replaced with 'B' for Buddhist year display.

## Key Imports

```typescript
import { SharedUtilitiesModule } from '@intersystems/shared-utilities';
import { fnCallPipe } from '@intersystems/shared-utilities';
import { ProvideDefaultPipe } from '@intersystems/shared-utilities';
import { SafeUrlPipe, SafeHtmlPipe } from '@intersystems/shared-utilities';
import { DayJSPipe } from '@intersystems/shared-utilities';
import { DayJSPipeConfigService } from '@intersystems/shared-utilities';
```
