# Revisión B independiente del harness #11 — r3

Revisé únicamente el delta de metadatos respecto de r2 y ejecuté el conjunto fake actualizado con `python3 -B`: 7/7 pasan. No ejecuté `--execute`, no abrí serial ni transmití UDP.

El manifiesto ahora describe con precisión las acciones: los controles UDP ejercitan GPIO; el replay exige la respuesta cacheada y no una reaplicación; el `UPDATE_TRIGGER` solicita una respuesta correlacionada `BUSY`; no se solicita escritura flash/OTA. También deja explícito que `boot_observed` valida identidad únicamente: recencia y reset no los comprueba el harness. El coordinador debe correlacionar el evento serial con la telemetría y garantizar un boot reciente antes de ejecutar. Estos cambios no amplían el alcance físico ni el de red.

El test nuevo rechaza `NaN`, infinitos y un timeout de 121 s antes de crear salida o abrir socket; en los cuatro casos verifica que no se transmite ni queda directorio de evidencia. El límite positivo de hasta 120 s cierra el achado de r2. No encontré una nueva reserva en el delta.

Hashes revisados: script `21a49cabd7cf53fb8d8a11fc7de16167aa04427ab45b5452ee0d58a0741d1812`, tests `6b91e0a3c119c7bfd74febc3774586dbf5aba80223544167a671447ceba7bf8d`, README `476e379ca0a70adb4bae206be64c36a2bc7d490e3b1a6f9106b32272b562e330`.

Parecer B: correcciones de metadatos y timeout aceptadas para revisión host. No constituye autorización ni resultado de ensayo en dispositivo.
