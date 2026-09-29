# Chart Type Configuration Examples

Configuration examples for each of the 8 chart types supported by `@intersystems/chart`.

## Bar Chart
```typescript
{ type: 'bar', title: 'Monthly Sales', labels: ['Jan', 'Feb', 'Mar', 'Apr'], legend: true,
  datasets: [{ data: [65, 59, 80, 81], label: 'Store A' }],
  options: { indexAxis: 'x', scales: { y: { label: 'Sales ($K)', min: 0 } } }, plugins: [] }
// Use indexAxis: 'y' for horizontal bars
```

## Line Chart
```typescript
{ type: 'line', title: 'Temperature Over Time', labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'], legend: true,
  datasets: [{ data: [20, 22, 19, 25, 23], label: 'High Temp', fill: true }],
  options: { noCurve: false, scales: { y: { label: 'Temperature (°C)' } } }, plugins: [] }
// noCurve: true for straight lines
```

## Radar Chart
```typescript
{ type: 'radar', title: 'Team Performance', 
  labels: ['Speed', 'Strength', 'Agility', 'Endurance', 'Intelligence'], legend: true,
  datasets: [{ data: [80, 70, 90, 60, 85], label: 'Player 1' }],
  options: { scales: { r: { min: 0, max: 100 } } }, plugins: [] }
```

## Polar Area Chart
```typescript
{ type: 'polarArea', title: 'Resource Distribution', labels: ['CPU', 'Memory', 'Disk', 'Network'],
  legend: true, datasets: [{ data: [75, 60, 85, 50], label: 'Usage %' }], options: {}, plugins: [] }
```

## Scatter Chart
```typescript
// import { ScatterDataPoint } from '@intersystems/chart';
{ type: 'scatter', title: 'Height vs Weight', labels: [], legend: true,
  datasets: [{ data: [{ x: 65, y: 150 }, { x: 70, y: 180 }] as ScatterDataPoint[], 
    label: 'Participants', pointRadius: 5 }],
  options: { scales: { x: { label: 'Height (in)' }, y: { label: 'Weight (lbs)' } } }, plugins: [] }
```

## Doughnut Chart
```typescript
{ type: 'doughnut', title: 'Market Share', labels: ['Product A', 'Product B', 'Product C'], 
  legend: true, datasets: [{ data: [35, 25, 20], label: 'Market %' }], options: {}, plugins: [] }
// Colors auto-assigned from Frost palette. Override with backgroundColor: ['#abc', '#def', ...]
```

## Pie Chart
```typescript
{ type: 'pie', title: 'Department Budget', labels: ['Engineering', 'Sales', 'Marketing', 'Support'],
  legend: true, datasets: [{ data: [500000, 300000, 200000, 150000], label: 'Budget ($)' }],
  options: {}, plugins: [] }
```

## Bubble Chart
```typescript
// import { BubbleDataPoint } from '@intersystems/chart';
{ type: 'bubble', title: 'Risk vs Return', labels: [], legend: true,
  datasets: [{ data: [{ x: 5, y: 12, r: 10 }, { x: 8, y: 15, r: 20 }] as BubbleDataPoint[], 
    label: 'Investments' }],
  options: { scales: { x: { label: 'Risk Score' }, y: { label: 'Expected Return (%)' } } }, plugins: [] }
// r = bubble radius in pixels
```

## Common Patterns

**Stacked Bar**: `options: { scales: { x: { stacked: true }, y: { stacked: true } } }`

**Horizontal Bar**: `options: { indexAxis: 'y' }`

**Hide Grid/Border**: `options: { toggleGrid: true, toggleBorder: true }` (default shown for bar/line, hidden for pie/doughnut/radar/polar)

**Responsive Sizing**: `options: { flex: true }` (overrides chartWidth/chartHeight)

**Hide Axis Labels**: `options: { hideXLabels: true, hideYLabels: true }`
