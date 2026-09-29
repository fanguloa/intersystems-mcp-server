---
name: uifw-text-diff
description: 'Compare and display text differences using @intersystems/text-diff. USE FOR: side-by-side text comparison, inline diff display, configuration change visualization, synchronized scroll diff viewing. DO NOT USE FOR: file-level git diffs, binary comparison, merge conflict resolution.'
argument-hint: 'Describe the diff display you need (format, content type, display options)'
---

# @intersystems/text-diff

Visualize differences between two text strings with side-by-side or line-by-line comparison.

## Quick Start

**Install:**
```bash
npm install @intersystems/text-diff
```

**Import module:**
```typescript
import { TextDiffModule } from '@intersystems/text-diff';

@NgModule({
  imports: [TextDiffModule]
})
```

**Provide content and template:**
```typescript
export class MyComponent {
  diffContent: DiffContent = {
    leftContent: 'Original text\nLine 2\nLine 3',
    rightContent: 'Modified text\nLine 2\nLine 3 updated'
  };

  onCompareResults(results: DiffResults) {
    console.log(`Found ${results.diffsCount} differences`);
  }
}
```

```html
<fr-text-diff
  [diffContent]="diffContent"
  (compareResults)="onCompareResults($event)">
</fr-text-diff>
```

## Component API

### Inputs

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `diffContent` | `DiffContent` | required | Content to compare: `{ leftContent: string, rightContent: string }` |
| `format` | `DiffTableFormat` | `'SideBySide'` | Display mode: `'SideBySide'` or `'LineByLine'` |
| `showToolbar` | `boolean` | `true` | Show format toggle and diff navigation toolbar |
| `hideMatchingLines` | `boolean` | `false` | Hide identical lines, show only differences |
| `synchronizeScrolling` | `boolean` | `true` | Sync scrolling between left and right (side-by-side only) |

### Outputs

| Output | Type | Description |
|--------|------|-------------|
| `compareResults` | `DiffResults` | Emitted after comparison: `{ hasDiff, diffsCount, rowsWithDiff[] }` |

## Display Modes

**Side-by-Side:** Split view with left content on left, right content on right. Deletions shown in red with minus, additions in green with plus. Best for comparing versions.

**Line-by-Line:** Unified view with deletions followed by additions. More compact for reviewing changes.

Use toolbar buttons to toggle between modes, or set `[format]` input.

## Key Features

**Diff navigation:** Toolbar prev/next buttons (or arrow keys, Alt+N/P) jump between differences.

**Hide matching lines:** Set `[hideMatchingLines]="true"` or use toolbar filter button to show only changed lines.

**Synchronized scrolling:** When enabled (default), scrolling one pane scrolls the other in side-by-side mode.

**Result analysis:** `compareResults` output provides diff count and line-level detail for custom logic.

## Key Imports

```typescript
import { TextDiffModule } from '@intersystems/text-diff';
import { DiffContent, DiffResults, DiffTableFormat } from '@intersystems/text-diff';
```
