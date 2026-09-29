# Breakpoint Observer Reference

The `FrBreakpointObserver` provides reactive breakpoint detection for responsive design in UIFW applications. It extends Angular CDK's `BreakpointObserver` with preconfigured Frost breakpoints.

## Import

```typescript
import { FrBreakpointObserver } from "@intersystems/styles";
```

The service is provided at root level, so just inject it:

```typescript
constructor(private bp: FrBreakpointObserver) {}
```

## Default Breakpoints

These are the Frost-approved default breakpoints (mobile-first, min-width):

| Breakpoint | Value | Description |
|------------|-------|-------------|
| `xs` | 400px | Extra small screens |
| `sm` | 840px | Small screens |
| `md` | 960px | Medium screens |
| `lg` | 1440px | Large screens |
| `xl` | 1920px | Extra large screens |

## API

### Breakpoint Observables

Pre-configured observables for each breakpoint emit `true` when the viewport matches that min-width:

```typescript
export class MyComponent {
  constructor(private bp: FrBreakpointObserver) {}

  ngOnInit() {
    this.bp.xs$.subscribe(matches => {
      console.log("xs breakpoint (400px+):", matches);
    });

    this.bp.md$.subscribe(matches => {
      console.log("md breakpoint (960px+):", matches);
    });
  }
}
```

Available observables: `xs$`, `sm$`, `md$`, `lg$`, `xl$`

### setBreakpoints(breakpoints: DefaultBreakpoints): void

Override default breakpoints with custom values.

```typescript
ngOnInit() {
  this.bp.setBreakpoints({
    xs: "320px",
    sm: "768px",
    md: "1024px",
    lg: "1280px",
    xl: "1536px"
  });
}
```

**Note:** This only affects the FrBreakpointObserver. It does not override breakpoints in Tailwind config or other parts of your application.

### observeBreakpoint(bpKey: keyof DefaultBreakpoints): Observable<boolean>

Observe a specific breakpoint by key.

```typescript
this.bp.observeBreakpoint("lg").subscribe(matches => {
  console.log("Large screen:", matches);
});
```

## Usage Patterns

### Conditional Rendering

```typescript
export class ResponsiveComponent {
  isMobile$ = this.bp.xs$;

  constructor(private bp: FrBreakpointObserver) {}
}
```

```html
<div *ngIf="isMobile$ | async">Mobile view</div>
<div *ngIf="!(isMobile$ | async)">Desktop view</div>
```

### Layout Switching

```typescript
export class DashboardComponent {
  layout$ = this.bp.md$.pipe(
    map(isDesktop => isDesktop ? "horizontal" : "vertical")
  );

  constructor(private bp: FrBreakpointObserver) {}
}
```

### Combining Breakpoints

```typescript
import { combineLatest } from "rxjs";
import { map } from "rxjs/operators";

export class AdvancedComponent {
  viewMode$ = combineLatest([
    this.bp.xs$,
    this.bp.md$,
    this.bp.xl$
  ]).pipe(
    map(([xs, md, xl]) => {
      if (xl) return "three-column";
      if (md) return "two-column";
      return "single-column";
    })
  );

  constructor(private bp: FrBreakpointObserver) {}
}
```

## Angular CDK Integration

`FrBreakpointObserver` extends `BreakpointObserver` from `@angular/cdk/layout`, so you can also use the underlying CDK methods:

```typescript
this.bp.observe("(min-width: 1200px)").subscribe(state => {
  console.log("Custom query:", state.matches);
});
```

## Relationship to Tailwind

The same breakpoint values are exported in the Tailwind config (`TAILWIND_FROST_CONFIG`) so your utility classes and JavaScript breakpoint detection stay in sync:

```typescript
import { DEFAULT_BREAKPOINTS } from "@intersystems/styles";

console.log(DEFAULT_BREAKPOINTS);
// { xs: "400px", sm: "840px", md: "960px", lg: "1440px", xl: "1920px" }
```
