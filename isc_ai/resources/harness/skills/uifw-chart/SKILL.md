---
name: uifw-chart
description: 'Create data visualizations using @intersystems/chart (Chart.js wrapper with Frost styling). USE FOR: bar charts, line charts, radar, polar area, scatter, doughnut, pie, bubble charts, ARIA-accessible data visualization. DO NOT USE FOR: complex dashboards, real-time streaming, maps.'
argument-hint: 'Describe the chart you need (type, data shape, options)'
---

# @intersystems/chart

Build accessible, Frost-styled data visualizations using the `@intersystems/chart` library. This is an Angular wrapper around Chart.js with InterSystems design system integration and ARIA support.

## Quick Start

1. **Install and import ChartModule**:
```typescript
// app.module.ts
import { ChartModule } from '@intersystems/chart';
import { NgChartsModule } from 'ng2-charts';

@NgModule({
  imports: [ChartModule, NgChartsModule, ...]
})
```

2. **Define ChartConfig in component**:
```typescript
import { ChartConfig, Dataset, Options } from '@intersystems/chart';

chartConfig: ChartConfig = {
  type: 'bar',
  title: 'Sales by Quarter',
  labels: ['Q1', 'Q2', 'Q3', 'Q4'],
  legend: true,
  datasets: [
    { data: [120, 150, 180, 200], label: '2024 Sales' }
  ],
  options: {
    scales: { y: { label: 'Revenue ($K)', min: 0 } }
  },
  plugins: []
};
```

3. **Add to template**:
```html
<fr-chart [chartConfig]="chartConfig" [Id]="'my-chart'"></fr-chart>
```

## Component API

### Inputs
- `[chartConfig]`: ChartConfig - full chart configuration (required)
- `[Id]`: string - unique identifier for canvas element

### ChartConfig
Core properties: `type` (8 chart types), `title`, `labels` (array), `legend` (boolean), `datasets` (Dataset[]), `options` (Options), `plugins` (any[]), optional `subtitle` and `asyncData` (Observable).

### Dataset
Required: `data` (number[] | ScatterDataPoint[] | BubbleDataPoint[]), `label` (string). Optional: `borderColor`, `backgroundColor`, `fill`, `type` (for mixed charts), `pointBackgroundColor`, `order`.

### Options
Common: `indexAxis` ('x'|'y'), `chartWidth` (default 900), `chartHeight` (default 450), `flex` (responsive), `noCurve` (straight lines), `borderWidth`, `scales.x/y` (min, max, label, stacked), `toggleGrid`, `toggleBorder`, `hideXLabels`, `hideYLabels`.

## Chart Types & Configuration

See `references/chart-types.md` for detailed examples of all 8 chart types:
- Bar, Line, Radar, Polar Area, Scatter, Doughnut, Pie, Bubble

## Key Features

- **Frost Styling**: Uses 8 high-contrast colors from `@intersystems/styles`
- **ARIA Accessible**: Screen-reader friendly with accessible HTML tables
- **Dark Mode Support**: Automatically updates grid/text colors on theme change
- **Async Data**: Supports Observable for dynamic updates via `asyncData`
- **Mixed Charts**: Specify `type` per dataset for multi-type visualizations

## Key Imports
```typescript
import { ChartModule } from '@intersystems/chart';
import { ChartConfig, Dataset, Options } from '@intersystems/chart';
import { NgChartsModule } from 'ng2-charts';  // Peer dependency
```
