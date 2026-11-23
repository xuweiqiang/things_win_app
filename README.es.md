# Cliente no oficial de Things para Windows

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md)

Cliente de escritorio independiente para Windows que lee y escribe en Things
Cloud mediante el protocolo público de la comunidad, manteniendo las tareas
sincronizadas con Things en macOS, iPhone y iPad.

> **Proyecto no oficial:** Este proyecto no está afiliado ni cuenta con el
> respaldo o la aprobación de Cultured Code. Things es una marca comercial de
> Cultured Code. El uso del protocolo privado de Cloud implica riesgos de
> compatibilidad y para la cuenta. Haz una copia de seguridad de los datos
> importantes antes de empezar.

## Funciones

- Ventana de escritorio nativa creada con pywebview y Microsoft Edge WebView2.
- Obtiene cambios remotos de Things Cloud y envía las ediciones de Windows al
  mismo history.
- Crea y edita tareas y proyectos, incluidos títulos, notas, fechas límite,
  etiquetas y pertenencia a proyectos o áreas.
- Vistas Inbox, Today, Upcoming, Calendar, Anytime, Someday, Logbook, Trash y
  proyectos.
- Calendario mensual con indicadores de fecha programada y fecha límite.
  Selecciona un día para ver sus tareas.
- Crea tareas en la fecha seleccionada del calendario o cambia cualquier fecha
  programada desde el panel de detalles.
- Pulsa `Ctrl+T` desde cualquier lugar para abrir el diálogo de nueva tarea y
  colocarla en Today.
- Completa, restaura, cancela, mueve a la papelera, restaura desde la papelera
  y reordena tareas.
- Caché local en disco, cola persistente para operaciones sin conexión,
  reintentos automáticos y una interfaz para resolver conflictos.
- Credenciales protegidas con Windows DPAPI y verificación PIN local opcional.
- Los archivos adjuntos se guardan localmente en Windows; en las notas solo se
  sincroniza el texto del marcador de adjunto.

## Aspectos aún no verificados

En esta versión no se han verificado las escrituras en una cuenta real de
Things Cloud ni la sincronización de ida y vuelta con iPhone. La verificación
actual abarca la API HTTP local, la cola persistente, la política de conflictos
y las confirmaciones bidireccionales contra un Things Cloud simulado.

Los siguientes son diseños de compatibilidad, no garantías probadas de
interoperabilidad:

- El cliente genera cargas completas de creación Task7 según las reglas del
  SDK Go de la comunidad.
- El cliente usa la identidad y el esquema de compatibilidad
  `ThingsMac/32209501` verificados por el SDK de la comunidad.
- Tras una confirmación correcta, los clientes de macOS/iOS que lean el mismo
  history deberían ver los cambios realizados en Windows.

Los escenarios aún no verificados con una cuenta real incluyen el inicio de
sesión en una cuenta existente, los avisos de seguridad de Apple, la
autenticación de dos factores, distintas versiones de Things/iOS, las tareas
recurrentes y los límites de frecuencia de Cloud. Usa una cuenta de prueba o
una copia de seguridad restaurable para la validación inicial.

## Requisitos

- Windows 10/11 x64.
- Python 3.10 o posterior para ejecutar desde el código fuente.
- Microsoft Edge WebView2 Runtime (normalmente incluido en Windows 10/11).
- Una cuenta activa de Things Cloud.

Instalación y inicio:

```powershell
python -m pip install -r requirements-dev.txt
python main_window.py
```

También puedes hacer doble clic en `启动.bat`. Si existe un ejecutable v3 en
`dist`, se inicia primero el EXE; de lo contrario, se ejecuta
`main_window.py`.

Para depurar solo el backend en un navegador:

```powershell
python app.py
```

El backend escucha únicamente en `127.0.0.1:55234` de forma predeterminada.

## Compilar el EXE

Haz doble clic en `build_onefile.bat` o ejecuta:

```powershell
python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name ThingsWindows_v3.0.4 `
  --add-data "index.html;." `
  --add-data "vue.global.prod.js;." `
  --add-data "tailwindcss.js;." `
  --collect-all webview `
  main_window.py
```

El ejecutable se genera en `dist\ThingsWindows_v3.0.4.exe`. `build/`,
`dist/`, los registros y los datos de ejecución no se confirman en Git.

## Pruebas

```powershell
python -m py_compile app.py things_cloud.py sync_engine.py main_window.py
python -m pytest -q
```

Las pruebas usan un Things Cloud simulado en memoria y nunca acceden a una
cuenta real. La cobertura incluye creación, edición, movimiento entre
proyectos y áreas, programación, fechas límite, etiquetas, finalización y
cancelación, papelera, reinicio de la cola sin conexión, combinación de
cambios remotos, conflictos en el mismo campo, reintento o descarte de
conflictos y recursos estáticos de la API.

## Comportamiento y límites de sincronización

- Las escrituras entran en una cola persistente en disco y solo salen de ella
  cuando Cloud responde correctamente.
- Las confirmaciones usan `ancestor-index`; ante un HTTP 409, el cliente
  obtiene primero los cambios remotos antes de decidir si existe un conflicto.
- Las ediciones simultáneas locales y remotas del mismo campo nunca se
  sobrescriben silenciosamente. La interfaz permite reintentar lo local o
  descartar lo local.
- Las ediciones simultáneas de campos distintos se combinan cuando es posible.
- Actualmente se rechazan las modificaciones de tareas recurrentes para evitar
  dañar el history.
- No se admite el borrado permanente; solo la papelera recuperable.
- El contenido de los adjuntos no se sube a Things Cloud. Solo se sincronizan
  los marcadores de adjunto de las notas.
- Las notas se guardan como objetos de texto sin formato de Things con una
  suma de comprobación CRC32.

Consulta las [notas de arquitectura y protocolo en chino](docs/ARCHITECTURE.md)
para obtener detalles de implementación.

## Ubicación de los datos

Los datos de la aplicación se guardan en `%APPDATA%\ThingsWindows\`. La caché
de cada history de Cloud se guarda en
`%APPDATA%\ThingsWindows\accounts\<history-hash>\`:

- `history_items.json`: caché local del estado de Cloud.
- `known_head.json`: último head remoto confirmado.
- `outbox.json`: operaciones de escritura pendientes de envío.
- `conflicts.json`: registros persistentes de conflictos.
- `sync_state.json`: último estado de sincronización.
- `credentials.dat`: credenciales cifradas con DPAPI en el directorio de la
  aplicación.
- `pins.json`: elementos fijados de la interfaz en el directorio de la
  aplicación.
- `attachments\`: adjuntos locales en el directorio de la aplicación.

Los registros se escriben en `things_debug.log`, junto al EXE o al código
fuente. Git ignora este archivo.

## Referencias y licencia

Referencias del protocolo de sincronización:

- [arthursoares/things-cloud-sdk](https://github.com/arthursoares/things-cloud-sdk)
- [evanpurkhiser/things3-cloud](https://github.com/evanpurkhiser/things3-cloud)

Este proyecto conserva la licencia MIT y los avisos de copyright del código de
la comunidad; consulta [LICENSE](LICENSE). Las compilaciones de navegador de
Vue y Tailwind incluidas también conservan sus respectivos avisos de licencia
MIT.
