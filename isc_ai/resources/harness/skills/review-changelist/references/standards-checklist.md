# Standards Checklist & Findings Classification

## Findings classification

### 🔴 Critical — block integration
- SQL injection (user input concatenated into SQL strings)
- Data corruption risks (mixed SQL/object saves, unsafe transactions)
- Missing error handling (`quit` without status)
- Backward compatibility breaks
- External calls within transactions
- `break` or debugger statements left in code

### 🟡 Standard violations — require fixes
- Variable/method names exceed 32 characters
- Missing parameter validation
- Hard-coded values instead of Parameters
- Naming convention violations
- Missing documentation on public methods
- Deprecated IRIS features

### 🟢 Observations — non-blocking
- Performance optimization opportunities
- Code clarity suggestions
- Testing recommendations
- Refactoring opportunities for the future

## Standards checklist

**Naming**
- Variables/methods ≤ 32 characters
- Row IDs: `ClassnameID` format
- Local vars: camelCase; parameters: CamelCase

**Code**
- Procedure blocks `{ }`
- Spacing: `if x = 1`, not `if x=1`
- Each command on a separate line

**Security**
- SQL parameters (no string concatenation with input)
- Parameter validation
- Error handling: `if $$$ISERR(sc) quit sc`

**Database**
- Objects saved with `%Save()` only
- Read-only: concurrency 0 `%OpenId(id, 0, .sc)`
- Avoid object loops (use SQL/GetStored)
- Close SQL result sets: `%Close()`
- Short transactions: `trollback 1`

**Properties**
- `%String` properties have MAXLEN
- Public methods documented with `///`
- SQL field names properly prefixed

## Alternate resources
- IntelliCare Lab Enterprise Coding Standards
- Database Access Patterns Guide
- Architecture Guidelines
- InterSystems Best Practices for ObjectScript
