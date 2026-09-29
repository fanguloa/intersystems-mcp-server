# Schematics and Upgrade Reference

The `@intersystems/schematics` package provides Angular upgrade migrations and tooling scripts for UIFW applications.

## Installation

```bash
npm install @intersystems/schematics --registry <artifactory url>
```

## Purpose

The schematics package centralizes upgrade logic so it doesn't need to be duplicated across ~30 UIFW libraries. It covers:

- Angular version upgrades
- Major library overhauls
- Changes required across multiple applications
- Helper generators for UIFW code

## Package Group

The `ng-update` migrations apply to all `@intersystems/*` and `@healthshare/*` packages. When you run `ng update @intersystems/schematics`, it will also update all other UIFW packages.

## Running ng-update Migrations

Upgrade to the latest compatible version:

```bash
ng update @intersystems/schematics
```

Upgrade to a specific version:

```bash
ng update @intersystems/schematics@18.0.0
```

Check what will be updated (dry run):

```bash
ng update @intersystems/schematics --allow-dirty --dry-run
```

## Available Migrations

### Placeholder Migration (v2999.0.0)

This is a skeleton migration intended for future use (e.g., Angular 18 to 19 upgrade). It does not perform any transformations yet.

## Available Schematics

### update-18

**One-time schematic** for upgrading from Angular 16 to Angular 18. This is an edge case that couldn't be handled through normal `ng update` flow.

```bash
ng g @intersystems/schematics:update-18
```

Run this if you're migrating a legacy app from Angular 16 directly to 18.

### enable-dark-mode

Scaffolds dark mode support in your application. Adds necessary configuration, imports, and components.

```bash
ng g @intersystems/schematics:enable-dark-mode
```

This schematic will:
- Configure the ThemingService for dark mode
- Add dark mode toggle to header (if using @intersystems/header)
- Update styles to support dark mode CSS classes
- Add dark mode persistence to localStorage

## Angular Version Requirements

The schematics package requires:
- `@angular/core` ^18.0.0
- `@angular/cli` ^18.0.0

## Manual Upgrade Steps

While the schematics handle most of the work, you may need to manually:

1. **Review breaking changes** in the UIFW changelog
2. **Update imports** if library APIs have changed
3. **Test thoroughly** after migration
4. **Update angular.json** if new build options are required
5. **Check peer dependencies** of all UIFW packages

## Troubleshooting

### Migration fails with "cannot find module"

Ensure all UIFW packages are at compatible versions. Run `npm ls @intersystems/*` to check for version mismatches.

### Dark mode schematic doesn't work

Verify you have `@intersystems/styles` installed and imported in `styles.scss`. The dark mode schematic depends on the styles library.

### ng-update suggests updating packages individually

Use the package group update to update all UIFW packages at once:

```bash
ng update @intersystems/schematics @intersystems/header @intersystems/table --force
```

## Future Migrations

As UIFW evolves, new migrations will be added for:
- Angular 18 to 19 upgrade
- Breaking API changes in UIFW libraries
- Material Design updates
- Accessibility improvements

Check the changelog for each release to see what migrations are included.
