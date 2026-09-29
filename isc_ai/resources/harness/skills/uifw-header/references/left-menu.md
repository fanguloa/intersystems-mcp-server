# Left Menu Navigation

Desktop sidebar. **Desktop only** — use hamburger for mobile.

## Basic Usage
```html
<fr-left-menu [config]="leftMenuConfig"></fr-left-menu>
```

```typescript
leftMenuConfig: LeftMenuConfig = {
  expanded: true,
  data: [
    { id: 'dashboard', name: 'Dashboard', iconName: 'dashboard', route: '/dashboard' },
    { id: 'admin', name: 'Admin', iconName: 'settings',
      children: [
        { id: 'users', name: 'Users', route: '/admin/users' },
        { id: 'roles', name: 'Roles', route: '/admin/roles' }
      ]
    }
  ],
  onItemClick: (node) => this.router.navigate([node.route])
};
```

## LeftMenuConfig
```typescript
interface LeftMenuConfig {
  data: AppNode[] | Observable<AppNode[]>;           // Required
  onItemClick: (node: FlatNode) => void;             // Required (leaf clicks)
  expanded?: boolean | Observable<boolean>;          // Default: false
  activeNodeId?: string | Observable<string>;
  onItemClickParent?: boolean;                       // Parent clicks. Default: false
  useSearch?: boolean | Observable<boolean>;         // Search bar. Default: false
  searchPlaceholder?: string;                        // Default: 'Search...'
  expandTooltip?: string; collapseTooltip?: string;
}
```

## AppNode
```typescript
interface AppNode {
  name: string; id: string;                          // Required
  iconName?: string;                                 // Material icon
  iconSVGName?: string;                              // SVG icon
  iconUrlPath?: string;                              // Image path
  route?: string;
  hidden?: boolean | (() => boolean);
  disabled?: boolean | (() => boolean);
  nodeCSSClass?: string; children?: AppNode[];       // Max 2 levels recommended
}
```

## Nested Navigation
```typescript
data: [{
  id: 'catalog', name: 'Catalog', iconName: 'inventory',
  children: [
    { id: 'products', name: 'Products', iconName: 'shopping_cart',
      children: [{ id: 'list', name: 'All', route: '/products' }]},
    { id: 'categories', name: 'Categories', route: '/categories' }
  ]
}]
```

## Search
```typescript
{ useSearch: true, searchPlaceholder: 'Search menu...' }
```
Filters by name (case-insensitive). Shows matches + parents/children.

## Active State / Expand Control
```typescript
activeNodeId: 'dashboard'  // Static
expanded: true             // Static

// Reactive
activeNodeId: this.router.events.pipe(filter(...), map(() => this.getCurrentNodeId()))
isExpanded = new BehaviorSubject<boolean>(true);
leftMenuConfig = { expanded: this.isExpanded.asObservable(), ... };
this.isExpanded.next(false);
```

## Click Handlers
```typescript
// Leaf nodes
onItemClick: (node) => { if (node.route) this.router.navigate([node.route]); }

// Parent nodes (with expand/collapse)
onItemClickParent: true,
onItemClick: (node) => { console.log('Clicked:', node.name); }
```

## Conditional Display
```typescript
data: [{
  id: 'admin', name: 'Admin', iconName: 'admin_panel_settings',
  hidden: () => !this.isAdmin(),
  children: [{ id: 'audit', name: 'Audit', route: '/audit', disabled: () => this.isReadOnly() }]
}]
```

## Observable Data
```typescript
menuData$ = this.menuService.getMenuStructure();
leftMenuConfig = { data: this.menuData$, onItemClick: (n) => this.nav(n) };
```

## Integration with Topbar
```html
<fr-topbar [appNameConfig]="app" [headerConfig]="header" [menusConfig]="menus">
  <fr-left-menu [config]="leftMenuConfig"></fr-left-menu>
  <router-outlet></router-outlet>
</fr-topbar>
```
