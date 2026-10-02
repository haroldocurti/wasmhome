# Inventário da bancada física

**Levantamento inicial:** 2026-10-01. Este registro consolida as informações fornecidas pelo usuário e as fotos da montagem. A identificação e a ligação dos componentes foram confirmadas pelo usuário; ainda não houve leitura funcional do sensor nem execução experimental.

## ESP32-S3

| Item | Registro | Evidência e estado |
|---|---|---|
| Variante | ESP32-S3-N16R8, encapsulamento QFN56 | Modelo informado pelo usuário e marcação visível nas fotos. |
| Revisão do silício | v0.2 | Tela de diagnóstico enviada pelo usuário; não é a revisão da placa portadora. |
| Flash | 16 MB | Tela de diagnóstico enviada pelo usuário. |
| PSRAM | 8 MB, AP Memory, 3,3 V | Tela de diagnóstico enviada pelo usuário. |
| CPU do ESP32-S3 | Dois núcleos, até 240 MHz | Tela de diagnóstico enviada pelo usuário. |
| Porta serial | COM12 | Porta observada pelo usuário em 2026-10-01; pode mudar ao reconectar. |
| Placa portadora | Modelo e revisão não identificados | A revisão v0.2 registrada acima é do silício. |

Na variante N16R8, GPIO33–GPIO37 são usados pela interface da PSRAM Octal e não devem ser destinados ao sensor ou ao loopback. O sensor foi encaminhado ao GPIO4.

## DS18B20 e loopback GPIO

O sensor disponível é um DS18B20 em encapsulamento TO-92. A montagem fotografada está na protoboard e foi confirmada pelo usuário em 2026-10-01.

Com a face plana do TO-92 voltada para o observador e os terminais para baixo, o mapeamento registrado é:

| Pino do DS18B20 | Sinal | Ligação | Cor informada |
|---|---|---|---|
| 1 | GND | GND da placa | Preto |
| 2 | DQ | GPIO4 | Amarelo |
| 3 | VDD | 3,3 V da placa | Vermelho |

Um resistor pull-up de 4,7 kΩ liga DQ/GPIO4 a 3,3 V. A pinagem e o pull-up seguem o [datasheet do DS18B20](https://www.analog.com/media/en/technical-documentation/data-sheets/DS18B20.pdf).

O loopback físico usa um jumper roxo entre GPIO5, configurado como saída de controle, e GPIO6, configurado como entrada observada. O usuário confirmou essa ligação.

## Alimentação, rede e observador

- O ESP32-S3 recebe alimentação por USB conectado ao PC. As fotos mostram a placa ligada.
- O usuário informou a rede Wi-Fi doméstica com SSID **hal9000**. A senha não foi registrada. A banda e o ponto de acesso ainda não foram identificados.
- O PC conectado à serial é usado como USB host. Modelo e sistema operacional não foram informados; seu papel como observador experimental ainda não foi confirmado.

## Host Proxmox

Leitura por SSH ao host em 2026-10-01, autorizada pelo usuário. Os detalhes abaixo descrevem o estado observado naquele momento.

| Item | Registro | Evidência e estado |
|---|---|---|
| Nome do host | `haroldo` | Confirmado via SSH. |
| Endereço de administração | `192.168.0.99:8006` | O endereço `192.168.0.99/24` está ativo em `vmbr0`; gateway padrão `192.168.0.1`. A porta `8006` foi informada pelo usuário. |
| Placa-mãe | Gigabyte GA-78LMT-S2P | Identificada via SMBIOS. |
| Sistema e kernel | Debian GNU/Linux 13 (trixie); `6.17.13-1-pve` | Confirmado via SSH. |
| Proxmox VE | `pve-manager` 9.1.6 | `pveversion` via SSH; revisão `71482d1833ded40a`. |
| Processador | AMD FX-6300, 6 núcleos, 1 socket, 1 thread por núcleo | Nome e topologia confirmados via SSH. |
| Memória instalada | 16 GB DDR3 | Informação fornecida pelo usuário. O sistema reporta 15 GiB utilizáveis. |
| Memória em uso na observação | 13 GiB usados; 1,9 GiB disponíveis; swap de 8,0 GiB, dos quais 6,3 GiB usados | Amostra pontual de `free -h` via SSH; carga pode mudar. Considerar antes de provisionar novos guests. |
| Discos detectados | 1 × Kingston SV300S37A240G (223,6 GiB); 2 × Kingston SA400S37240G (223,6 GiB cada); 1 × WDC WD10SPZX-00Z10T0 (931,5 GiB); 1 × ST2000DM006-2DM164 (1,8 TiB) | Modelos e capacidades reportados por `lsblk`. Isso não confirma espaço livre nem configuração como storage Proxmox. |
| LXC do servidor de artefatos | CT 107, wasmhome-bench, Debian 13, 1 CPU, 1.024 MiB RAM, 24 GiB em local-lvm | Criado com autorização em 01/10/2026; não privilegiado, nesting=1, DHCP 192.168.0.138, onboot desabilitado. Acesso por chave e persistência após reboot comprovados. Ver [registro do provisionamento](provisionamento-lxc.md). |

## Ambiente

- Local interno.
- Temperatura observada: 26 °C, informada pelo usuário em 2026-10-01.
- Umidade relativa: não medida; o instrumento disponível mede apenas temperatura.
- Repetir a leitura ambiental no início de cada sessão experimental; esta leitura registra somente o levantamento inicial.

## Limites deste registro

- O modelo, a revisão do silício, as capacidades de memória e a porta serial do ESP32 vêm das imagens e informações fornecidas pelo usuário; não foram lidos novamente a partir do hardware.
- A fiação está documentada conforme as fotos e a confirmação do usuário. Não foi executado teste de continuidade, leitura 1-Wire ou leitura funcional do loopback.
- SSID, senha/banda do Wi-Fi não foram verificados. O endereço IP, a rota, o hardware e as versões de software do Proxmox foram consultados ao vivo via SSH; o painel web não foi consultado.
- Ainda faltam o modelo/revisão da placa portadora, a identificação do ponto de acesso e a confirmação da banda Wi-Fi. O LXC foi criado; falta comprovar conectividade com o ESP32 e reconstrução separada. O usuário confirmou este PC como observador, na mesma rede local do ESP32.
