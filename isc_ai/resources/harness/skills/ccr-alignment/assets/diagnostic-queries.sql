-- =============================================================
-- CCR ALIGNMENT - DIAGNOSTIC QUERIES
-- Reemplazar los parámetros :WIN_RowId, :CON_Childsub, :RESP_Childsub
-- con los valores extraídos del mensaje de error.
-- Formato del error: WIN||CON_Childsub||RESP_Childsub
-- =============================================================

-- -------------------------------------------------------
-- FASE 1: CONTEXTO COMPLETO (ejecutar TODO antes de actuar)
-- -------------------------------------------------------

-- 1a. Ventana completa
SELECT WIN_RowId, WIN_Code, WIN_Desc, WIN_Type, WIN_Status, WIN_Category
FROM SQLUser.SS_UserDefWindow
WHERE WIN_RowId = :WIN_RowId;

-- 1b. Todos los controles de la ventana (panorama de estructura)
SELECT CON_RowId, CON_Childsub, CON_Code, CON_Text, CON_ControlType, CON_Mandatory
FROM SQLUser.SS_UserDefWindowControls
WHERE CON_ParREf = :WIN_RowId
ORDER BY CON_Childsub;

-- 1c. Todas las responses de la ventana (qué opciones existen)
SELECT c.CON_Childsub, c.CON_Code, c.CON_Text,
       r.RESP_RowId, r.RESP_Childsub, r.RESP_Code, r.RESP_Desc
FROM SQLUser.SS_UserDefWinContResp r
JOIN SQLUser.SS_UserDefWindowControls c ON r.RESP_ParRef = c.CON_RowId
WHERE c.CON_ParREf = :WIN_RowId
ORDER BY c.CON_Childsub, r.RESP_Childsub;

-- -------------------------------------------------------
-- FASE 2: AISLAMIENTO DEL ELEMENTO BLOQUEANTE
-- -------------------------------------------------------

-- 2a. Control específico bloqueante
SELECT CON_RowId, CON_Childsub, CON_Code, CON_Text,
       CON_ControlType, CON_Mandatory, CON_Default
FROM SQLUser.SS_UserDefWindowControls
WHERE CON_ParREf = :WIN_RowId AND CON_Childsub = :CON_Childsub;

-- 2b. Response específica bloqueante
SELECT RESP_RowId, RESP_Childsub, RESP_Code, RESP_Desc, RESP_Score
FROM SQLUser.SS_UserDefWinContResp
WHERE RESP_ParRef->CON_ParREf = :WIN_RowId
  AND RESP_ParRef->CON_Childsub = :CON_Childsub
  AND RESP_Childsub = :RESP_Childsub;

-- -------------------------------------------------------
-- FASE 3: IMPACTO - cuántos y quiénes respondieron
-- Tabla real confirmada en UAT (2026-05-20): SS_UserDefWinContRespAct
-- Prefijo de columnas: ACT_   (NO SS_UserDefWinContAnswer / ANS_ — no existe)
-- Global de storage: ^SSU
-- -------------------------------------------------------

-- 3a. Cantidad total de activaciones que referencian esta response
SELECT COUNT(*) AS TotalAnswers
FROM SQLUser.SS_UserDefWinContRespAct
WHERE ACT_ParRef = :WIN_RowId||'||'||:CON_Childsub||'||'||:RESP_Childsub;

-- 3b. Muestra de activaciones
SELECT TOP 20
       a.ACT_RowId,
       a.ACT_Childsub,
       a.ACT_Control_DR,
       a.ACT_Score
FROM SQLUser.SS_UserDefWinContRespAct a
WHERE a.ACT_ParRef = :WIN_RowId||'||'||:CON_Childsub||'||'||:RESP_Childsub;

-- 3c. Verificar si la response existe en la tabla (puede haber sido ya eliminada)
SELECT COUNT(*) AS RespExiste
FROM SQLUser.SS_UserDefWinContResp
WHERE RESP_RowId = :WIN_RowId||'||'||:CON_Childsub||'||'||:RESP_Childsub;

-- -------------------------------------------------------
-- FASE 4: ELIMINACIÓN (solo después de revisar Fases 1-3
--          y confirmar con el usuario que es seguro)
-- -------------------------------------------------------

-- 4a. Eliminar activaciones primero (OBLIGATORIO antes de eliminar la response)
-- Tabla real observada: SS_UserDefWinContRespAct / prefijo ACT_
DELETE FROM SQLUser.SS_UserDefWinContRespAct
WHERE ACT_ParRef = :WIN_RowId||'||'||:CON_Childsub||'||'||:RESP_Childsub;

-- 4b. Recién entonces eliminar la response bloqueante
DELETE FROM SQLUser.SS_UserDefWinContResp
WHERE RESP_ParRef->CON_ParREf = :WIN_RowId
  AND RESP_ParRef->CON_Childsub = :CON_Childsub
  AND RESP_Childsub = :RESP_Childsub;

-- -------------------------------------------------------
-- FASE 5: VERIFICACIÓN POST-DELETE
-- -------------------------------------------------------

-- 5a. Confirmar que la response ya no existe
SELECT COUNT(*) AS QueDebeSerCero
FROM SQLUser.SS_UserDefWinContResp
WHERE RESP_ParRef->CON_ParREf = :WIN_RowId
  AND RESP_ParRef->CON_Childsub = :CON_Childsub
  AND RESP_Childsub = :RESP_Childsub;

-- 5b. Re-lanzar la carga del XML y confirmar que no hay más errores #5001
