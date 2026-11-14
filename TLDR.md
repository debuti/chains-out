**chainsout** busca ofertas de trabajo remoto que encajen con tu perfil y te avisa por Telegram.

- **Fuentes**: LinkedIn/Indeed (JobSpy), HN Who is hiring, bolsas remotas (RemoteOK, Remotive, WWR, Himalayas) y páginas de empleo de empresas (Greenhouse/Lever/Ashby).
- **Prefiltro**: reglas baratas (títulos, part-time, sin remoto, empresas excluidas) antes de gastar LLM.
- **Clasificador**: Claude juzga cada oferta contra tus criterios y le da match/maybe/reject y nota.
- **Telegram**: avisos con 👍/👎, resumen semanal, aviso si Claude se queda sin uso (y espera a que vuelva) y comandos /pausar, /reanudar, /estado.
- **Ejecución**: GitHub Actions 6 veces al día (o daemon/cron local); estado cifrado en la caché.
- **Config**: tu perfil, búsquedas y empresas son locales o van en un secret; el repo solo trae ejemplos de varios perfiles (examples/).
