---
name: uifw-types
description: 'Import shared type definitions from @intersystems/types. USE FOR: importing shared interfaces used across table/filter-widget/isc-form, resolving circular dependency issues between UIFW packages, accessing consolidated type exports. DO NOT USE FOR: defining new types, component implementation, runtime behavior.'
argument-hint: 'Describe which shared types you need (table, filter, form interfaces)'
---

# @intersystems/types

Consolidated type definitions and interfaces from filter-widget, isc-form, and table packages.

## Purpose

Resolves circular dependencies between UIFW packages by providing a shared location for type imports.

**Example**: @intersystems/table uses runtime imports of @intersystems/isc-form for editable cells, but @intersystems/isc-form depends on @intersystems/table for table field types. @intersystems/types breaks this cycle by re-exporting interfaces from both packages.

## Quick Start

```typescript
import { interfaces, constants } from '@intersystems/types';
```

Or import specific types directly:

```typescript
import { 
  FilterParam, 
  FilterParamGroup,
  TableConfig,
  IscFormFieldConfig,
  FIELD_TYPES,
  VALIDATORS 
} from '@intersystems/types';
```

## Re-Exported Interfaces

### From filter-widget
- `FilterParam` - Display text and coded value for filter options
- `FilterParamGroup` - Group of related filter parameters with label and key

### From isc-form
- `IscFormFieldConfig` - Field configuration for Formly forms
- `HealthShareFieldAddons` - HealthShare-specific form field extensions
- `FIELD_TYPES` - Constants for field type identifiers
- `IscFormDateFormats` - Date format constants
- `FormModes` - Form mode constants (view/edit)
- `PasswordRegExs` - Password validation regex patterns
- `VALIDATORS` - Validator constants
- `BreakPoints` - Responsive breakpoint constants

### From table
- `TableConfig` - Table configuration interface
- `CheckboxConfig`, `EditableCellConfig`, `NewButton` - Table feature configs
- `PaginatorConfig`, `TableSearchConfig`, `TableSortConfig` - Table UI configs
- `AbstractTableCellComponent` - Base class for custom cell components
- Table constants

## Version Management

When editing an interface in this package, **both** @intersystems/types **and** the source package must have their versions bumped together.

## Key Imports

```typescript
import { FilterParam, FilterParamGroup } from '@intersystems/types';
import { TableConfig, AbstractTableCellComponent } from '@intersystems/types';
import { IscFormFieldConfig, FIELD_TYPES, VALIDATORS } from '@intersystems/types';
```
