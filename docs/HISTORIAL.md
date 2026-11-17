# Historial de decisiones

Resumen de la conversación (17-nov-2026) en la que se diseñó y construyó Tríada.

1. **Investigación inicial**: qué necesita una web para optimizarse en SEO, SEM y GEO (E-E-A-T, Core Web Vitals,
   datos estructurados, Nivel de Calidad, Consent Mode, conversiones; para GEO, la guía de Google de mayo de 2026
   dice que AI Overviews se basa en el SEO normal, y los estudios muestran que la mayoría de menciones de marca en
   respuestas de IA vienen de webs de terceros).
2. **Producto**: herramienta que audita y corrige la web en las tres vertientes, por separado o a la vez
   (modo combinado con resolución de conflictos).
3. **Google Ads API**: el developer token se retiró el 9-sep-2026; el acceso se pide desde Google Cloud.
   El usuario ya tiene el proyecto de Google Cloud verificado (300 € de crédito de prueba).
4. **v0.1** motor de línea de comandos (rastreo, auditorías, correcciones, informe).
5. **v0.2** app local de doble clic: panel, áreas de análisis y solución por rama, informes exportables,
   historial y web de demostración. Aplicar en carpeta con copia de seguridad y deshacer, o paquete .zip.
6. **v0.3** el usuario señaló que SEM y GEO dependen de factores externos y que la nota interna engañaba.
   Se acordó: nota en tres capas con cobertura, «sin datos» en vez de inventar, y un plan de acciones fuera de
   la web generado a partir de datos (cuestionario, CSV de Google Ads y Search Console, monitor de IA con APIs
   oficiales, reseñas de Google Places, Wikidata), con estados y verificación automática.
   Pesos elegidos por defecto al no responder el usuario: SEM 40/60, GEO 30/70 (y SEO 60/40, retirado en 0.4).
7. **v0.4** a petición del usuario: SEO pasa a ser solo interno; en Solucionar se pueden subir archivos HTML
   (sueltos o .zip) y descargar el HTML modificado; exportación de resultados desde Solucionar.
8. El proyecto pasa a Claude Code para seguir el desarrollo.
