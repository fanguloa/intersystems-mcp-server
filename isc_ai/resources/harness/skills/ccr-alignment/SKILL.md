---
name: ccr-alignment
description: "Trigger: error #5001 CCR, alineación de CCR, SSUserDefWindow load error, Questionnaire Control Response can't be deleted, GUID reference missing, Questionnaire changes not allowed because it's been answered, Unique Index conflict, ERROR #5808 clave no es unica, ClassNameRowIDIndex, User.CTOverride. Diagnosticar y resolver el bloqueo de carga de XMLs (SSUserDefWindow, CTOverride y otras CT tables) en InterSystems IRIS cuando una response fue respondida, un cuestionario ya tiene respuestas, un GUID referenciado no existe o el registro existe pero le falta el mapeo ^OBJ.GUID en el ambiente destino."
license: Apache-2.0
metadata:
  author: fangulo
  version: "1.5"
---

## Activation Contract

Activar cuando aparezca cualquiera de estos errores:
- `ERROR #5001: Questionnaire Control Response X||Y||Z can't be deleted after it's been answered`
- `Unable to load stream .../SSUserDefWindow/....xml`
- `ERROR #5001: GUID reference(s) missing from stream .../questionnaire.*.xml; <GUID>`
- `ERROR #5001: Questionnaire Control changes are not allowed because it's been answered. $lb("...","User.SSUserDefWindowControls"), <GUID>`
- `WARNING: Unique Index conflict. User.CTOverride <GUID> / Conflict with: User.CTOverride <ID>` + `ERROR #5808: La clave no es única: User.CTOverride:ClassNameRowIDIndex`

## Variante: Unique Index conflict en CT tables (User.CTOverride, etc.)

### Error típico
```
WARNING: Unique Index conflict.  User.CTOverride    D2732B3F-AAED-11F1-B5F2-6045BDD2E731
    Conflict with: User.CTOverride  2895
ERROR #5808: La clave no es única: User.CTOverride:ClassNameRowIDIndex:^CT("OVR",0,"ClassNameRowIDIndex"," EPR.GROUPSETTINGS"," 27")
Transaction rolled back
```

### Causa
El registro destino (ID 2895, ClassName+RowID) YA EXISTE en el ambiente, pero **no tiene GUID registrado en `^OBJ.GUID`**. El loader busca el GUID del XML, no lo encuentra, decide INSERT en vez de UPDATE y choca contra el índice único. Como es transaccional, **todo el ItemSet se revierte** (ningún ítem queda cargado).

Señal inequívoca: el bloque `<Information>` del XML fallido trae `<Id>N</Id>` igual al ID del "Conflict with". Frecuentemente los hijos (`User.CTOverrideField` `N||1`) SÍ tienen GUID mapeado — registro GUID parcial de una carga anterior a medias.

### Diagnóstico (solo lectura)
```objectscript
; storage real: datos en ^CT("OVR",1,id), indices en ^CT("OVR",0,...)
Write $ListToString(^CT("OVR",1,2895),"|")                      ; existe?
Write $Get(^OBJ.GUID(1,$lb("2895","User.CTOverride")))          ; vacio = falta mapeo
Write $ListToString($Get(^OBJ.GUID(0,"<GUID-DEL-XML>")),"|")    ; vacio = falta mapeo
```
Cuidado con `$lb`: el subindice de `^OBJ.GUID(1,...)` usa el ID como **string** (`$lb("2895",...)`), no numerico. `%GUID()` sin argumentos devuelve vacio aun con mapeo correcto — usar `##class(%Library.GUID).%GUIDFind(guid)` para verificar.

### Fix
1. Registrar el mapeo bidireccional (mismo patron que "Fix ^OBJ.GUID"):
   ```objectscript
   SET ^OBJ.GUID(0,"<GUID-DEL-XML>") = $lb("<ID>","User.CTOverride")
   SET ^OBJ.GUID(1,$lb("<ID>","User.CTOverride")) = "<GUID-DEL-XML>"
   ```
2. Re-ejecutar la carga. Si el ItemSet ya se importo y LoadToOS fue OK, basta con `LoadToNS`:
   ```objectscript
   Set is=##class(%Studio.SourceControl.ItemSet).%OpenId(<ItemSetID>)
   Set sc=is.LoadToNS(,.loaded,0,1,.pct,.proj)   ; pCompileProject=1
   ```
3. Verificar en `is.Log` que aparece `Load finished` sin `Load detected N errors`.

### NO hacer
Borrar el registro existente: el `%OnDelete` en cascada elimina los hijos `CTOverrideField`, dejando sus GUIDs huerfanos en `^OBJ.GUID` → siguiente carga falla con "GUID reference missing".

Caso real: UAT de un despliegue con CCR activo, ItemSet 1113, 2026-09-08.

## Query Execution Policy

Cuando haya conexión a IRIS disponible (endpoint REST, `iris` CLI, o MCP tool configurado):
- **Ejecutar las queries directamente** sin pedirle al usuario que las copie.
- Usar la skill `iris-class` si está disponible para correr ObjectScript/SQL.
- Si no hay conexión configurada, proveer las queries listas para copiar e indicarlo explícitamente.

## Variante: GUID reference missing

Cuando el error es `GUID reference(s) missing from stream`, el GUID referenciado no existe en el ambiente destino. Pasos:

### 1. Identificar el GUID faltante

Extraer del mensaje: `...xml; <GUID-FALTANTE>`

### 2. Buscar en qué tabla vive ese GUID (ejecutar en UAT y en PROD/origen)

```sql
SELECT 'RESP' AS tipo, RESP_RowId AS id, RESP_Name AS nombre, RESP_WinRef AS winRef
FROM SQLUser.SS_UserDefWinContResp
WHERE RESP_RowId = '<GUID>'
UNION ALL
SELECT 'CON', CON_RowId, CON_Name, CON_WinRef
FROM SQLUser.SS_UserDefWinControl
WHERE CON_RowId = '<GUID>'
UNION ALL
SELECT 'WIN', WIN_RowId, WIN_Name, WIN_Code
FROM SQLUser.SS_UserDefWindow
WHERE WIN_RowId = '<GUID>'
```

### 3. Interpretar resultados

| Resultado en UAT | Resultado en PROD | Acción |
|---|---|---|
| No existe | Existe | El objeto no se incluyó en el CCR — agregarlo al deploy |
| No existe | No existe | El objeto fue eliminado en origen — revisar si los XMLs que lo referencian deben actualizarse |
| Existe | Existe | El objeto existe pero el mapeo GUID falta — ver sección **Fix ^OBJ.GUID** |

### 4. Resolución más común

Si el GUID existe en PROD pero no en UAT: el objeto (response, control, o window) faltó en el CCR. Opciones:
- Incluirlo en el deploy actual
- O si fue eliminado intencionalmente en PROD, actualizar los XMLs referenciantes para quitar esa referencia

### Fix ^OBJ.GUID — GUID reference missing con objeto ya existente

Cuando el objeto referenciado SÍ existe en UAT (por ejemplo, un `websys.Component` con su `ID` numérico) pero el CCR igual falla con GUID missing, el problema es que **el mapeo `^OBJ.GUID` no está registrado**.

En TrakCare/IRIS, los objetos con `GUIDENABLED = 1` (como `websys.Component`) tienen dos nodos en `^OBJ.GUID`:

```objectscript
; Nodo 0: GUID → objeto
^OBJ.GUID(0,"<GUID>") = $lb("<ID>","<ClassName>")

; Nodo 1: objeto → GUID (índice inverso)
^OBJ.GUID(1,$lb("<ID>","<ClassName>")) = "<GUID>"
```

**Diagnóstico**: verificar si el mapeo existe

```objectscript
WRITE $Data(^OBJ.GUID(0,"3E51A17E-489E-11F1-82FD-6045BDD2E731"))
; 0 = no existe → hay que registrarlo
; 1 = existe → el CCR puede estar desactualizado, re-ejecutar la carga
```

**Fix**: registrar los dos nodos

```objectscript
; Reemplazar <ID> por el ID real del objeto en UAT y <ClassName> por la clase
SET ^OBJ.GUID(0,"<GUID>") = $lb("<ID>","<ClassName>")
SET ^OBJ.GUID(1,$lb("<ID>","<ClassName>")) = "<GUID>"
```

Ejemplo real (UAT de un despliegue, 2026-05-20):
```objectscript
; websys.Component "questionnaire.QACHSPASSPO.Edit" tiene ID=54024 en UAT
SET ^OBJ.GUID(0,"3E51A17E-489E-11F1-82FD-6045BDD2E731") = $lb("54024","websys.Component")
SET ^OBJ.GUID(1,$lb("54024","websys.Component")) = "3E51A17E-489E-11F1-82FD-6045BDD2E731"
```

Para encontrar el ID del objeto existente:

```sql
-- websys.Component
SELECT ID, Name FROM websys.Component WHERE ClassName = 'questionnaire.QACHSPASSPO'

-- websys.Preferences (para otros tipos)
SELECT ID FROM websys.Preferences WHERE ... 
```

Después de registrar el mapeo, **re-ejecutar la carga del CCR** — los errores GUID missing deben desaparecer.


## Variante: Cambios en Controles del Cuestionario No Permitidos (`Questionnaire Control changes are not allowed because it's been answered`)

### Error Típico
```
ERROR #5001: Questionnaire Control changes are not allowed because it's been answered. $lb("355||250","User.SSUserDefWindowControls"), E5287898-5BF2-11EB-8414-005056BC03FE
> ERROR #5001: Unable to load stream /trak/<site>/live/ccr/custom_ccrs/cl/<SITE>/<version>/LIVE/misc/User/SSUserDefWindow/1D4EB42E-646F-11E1-97DE-0303B49F70A5.xml
```

### Causa
El instalador del CCR está intentando aplicar cambios en la definición de la estructura o controles de un cuestionario (`User.SSUserDefWindowControls` o `User.SSUserDefWindow`), pero el cuestionario en el ambiente destino ya fue contestado/respondido por pacientes o clínicos, y el motor de TrakCare bloquea la modificación para prevenir inconsistencias de datos históricos de encuestas.

### Resolución (UnlockQuest)

Para destrabar el instalador y permitir la carga de cambios en el cuestionario:

1. **Obtener el WINCODE del cuestionario:**
   Abre el archivo XML del error (ej: `1D4EB42E-646F-11E1-97DE-0303B49F70A5.xml`) y busca la etiqueta `<WINCode>` (ej: `<WINCode>UNCODIGO</WINCode>`). El valor dentro de la etiqueta es el código único del cuestionario (`WINCODE`).
2. **Ejecutar Rutina de Desbloqueo (UnlockQuest):**
   Ejecuta el siguiente comando ObjectScript desde el terminal o mediante un comando en IRIS. Este comando suspende temporalmente las restricciones de Change Control y ejecuta la rutina de desbloqueo custom de TrakCare:
   ```objectscript
   do ##class(TC.ChangeControl.Interface).Suspend()
   do Execute^UCLXX.UnlockQuest("WINCODE")
   ```
   *(Reemplaza `UCLXX` por la rutina de personalización del proyecto destino, por ejemplo `U<SITE>`, con el código de dos a cuatro letras del proyecto destino., y `"WINCODE"` por el código extraído en el paso anterior, ej: `"UNCODIGO"`).*

## Schema confirmado (UAT de un despliegue, 2026-05-20)

| Elemento | Nombre real |
|---|---|
| Tabla de activaciones/answers | `SQLUser.SS_UserDefWinContRespAct` |
| Prefijo de columnas | `ACT_` |
| Campo ParRef | `ACT_ParRef` (valor: `WIN\|\|CON\|\|RESP`) |
| Global de storage | `^SSU("WIN", win, "CON", con, "RESP", resp)` |
| Tabla de responses | `SQLUser.SS_UserDefWinContResp` |

> `SS_UserDefWinContAnswer` con prefijo `ANS_` **no existe** en este schema.

## Hard Rules

- NUNCA eliminar una response directamente sin verificar activaciones primero — el `%OnDelete` de IRIS lo bloqueará con el mismo error.
- SIEMPRE ejecutar en UAT antes de PROD.
- SIEMPRE confirmar con el usuario que la response (y sus activaciones) son descartables antes de cualquier DELETE.
- Si la response ya no existe en `SS_UserDefWinContResp`, verificar que `WIN_LastUpdateDate` sea posterior al error — puede que el CCR ya se cargó exitosamente.

## Execution Steps

### 1. Leer el error y extraer los identificadores

Formato: `Questionnaire Control Response WIN||CON_Childsub||RESP_Childsub`

Del XML fallido, extraer el GUID → resolver `WIN_RowId` en la BD.

### 2. Fase 1–2 del asset: contexto completo + aislamiento

Ejecutar `assets/diagnostic-queries.sql` Fases 1 y 2. Entender:
- Qué ventana clínica es (código, descripción, tipo)
- Qué control y qué respuesta específica están bloqueando

### 3. Fase 3 del asset: impacto de las activaciones

**Primero verificar si la response aún existe** (Fase 3c del asset). Si no existe y `WIN_LastUpdateDate` es posterior al error → el CCR ya se cargó exitosamente, no hay nada que hacer.

Si la response existe, ejecutar conteo de activaciones. Antes de cualquier DELETE, el usuario debe saber:
- Cuántas activaciones (`SS_UserDefWinContRespAct`) referencian esa response
- Muestra de los registros (TOP 20)

| Resultado count | Acción |
|----------------|--------|
| COUNT = 0 | Ir directo a Fase 4b (DELETE response solamente) |
| COUNT > 0 | Mostrar impacto al usuario y pedir confirmación explícita antes de continuar |

### 4. Fase 4 del asset: eliminación en orden estricto

1. DELETE activaciones (4a, tabla `SS_UserDefWinContRespAct`) — OBLIGATORIO si COUNT > 0
2. DELETE response (4b) — solo después de que 4a complete sin errores

### 5. Fase 5: verificación

- Confirmar COUNT = 0 en la response eliminada
- Re-lanzar la carga del XML y verificar que no aparece más el error #5001

### 6. Si el DELETE de activaciones también falla

La tabla `SS_UserDefWinContRespAct` tiene su propia constraint. Escalar a ObjectScript:
```objectscript
##class(%Dictionary.ClassDefinition).%OpenId("User.SSUserDefWinContRespAct")
```
Revisar `%OnDelete` / `%OnDeleteFinally` en la clase. Buscar la referencia al global `^SSU`.

## Output Contract

- Confirmar qué WIN/CON/RESP se resolvió
- Confirmar que la carga del XML pasó sin errores
- Documentar si hubo answers eliminados (cantidad y tabla)

## References

- `assets/diagnostic-queries.sql` — queries parametrizadas para diagnóstico y limpieza
