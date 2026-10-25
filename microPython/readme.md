# MicroPython no openDecabot
![MicroPython Logo](/images/MicroPython.png)

Para o uso do microPython no openDecabot estmaos escolhendo unificar os tutoriais com a microcotroladora **ESP32 C3**. Apesar de termos tido sucesso também com o Wemos D1 Mini, a plataforma ESP32, com WiFi e Bluetooth aumentam as possibilidades de uso, além da maior capacidade de processamento.

A escolha do ESP32 C3 também é feita por questões de didática, já que usar outras plataformas podem trazer chaetações e problemas com drivers, especialmente placas que usam o chip CH341 para a porta USB e sua infinita chatice com o Windows de ficar instalando e reinstalando os drivers (esse problema não ocorre em quem usa Linux). 
## Vantagens do microPython ao invés da programação Arduino C
A implementação do micrpPython traz muitas vantagens para quem está aprendendo. Obviamente para projetos complexos, que exigem o maior desempenho possível, recomenda-se escrever os códigos em C direto. Mas para quem está iniciando aqui vão alguns pontos a se tocar:
### A controladora funciona como um disco com diferentes arquivos
Sua microcontroladora ESP32 C3 (ou outra qualquer) funciona como um disco cheio de arquivos .py para usar. Inclusive com pastas para organizar, e com arquivos especiais para executar assim que a placa é ligada (`boot.py`). Ou seja, sua placa pode tornar-se um repositório de códigos que você vem escrevendo e testando sempre a mão, para reutilizar. 
### Só se grava a firmware uma vez
O processo de gravar o firmware inteiro como se faz na IDE do Arduino é muito lento, arriscado e pouco prático. No microPython você só vai acrescentando novos arquivos .py a sua coleção! E ao conectar seu robô/placa na IDE você pode chamar diferentes arquivos para executar. Isso permite, por exemplo que eu tenha diferentes configurações gravadas de um robô, por exemplo, que podem ser carregadas para diferentes competições. 
### Terminal Python REPL
O python tem um terminal de comandos que podem ser executados diretamente. É o chamado REPL:
-   **Read (Ler):** Recebe o código digitado pelo usuário.

-   **Eval (Avaliar/Executar):** Processa e executa essa instrução.

-   **Print (Imprimir):** Mostra o resultado da execução no terminal.

-   **Loop (Repetir):** Retorna ao estado inicial, aguardando o próximo comando
O estudante pode experimentar comandos no terminal, ver ações, ler sensores ou mover motores diretamente, sem precisar compilar e gravar a firmware. 

## Preparando seu ESP32 C3

Para usar o microPython você precisa gravar uma firmware específica no seu microcontrolador pela primeira vez. Existem várias formas de fazer isso, e você pode buscar outros tutorias mais completos caso deseje. Para nós, o melhor caminho foi:
- Baixar a release mais nova da ESPTOOL: https://github.com/espressif/esptool/releases
- Baixar o arquivo bin da controladora: https://micropython.org/download/
	- No caso do ESP32 C3 o link é esse: https://micropython.org/download/ESP32_GENERIC_C3/
	- Baixe a página até a sessão _Firmware Releases_ para encontrar o arquivo mais novo.
	- Coloque o arquivo .bin baixado **na mesma pasta** do programa ESPTOOL, para facilitar os comandos!
- Colocar a controladora na USB e ver qual porta está:
	- Para isso usamos a IDE do Arduino para verificar qual porta COM (Win) ou /dev (Linux)
- Apagar a FLASH da controladora:
	- WIN (_substitua a porta COM com a sua_): `esptool --port com16 erase_flash`
	- LINUX (_substitua a porta /dev com a sua_):	`.\esptool --port /dev/ttyUSB0 erase_flash`
- Gravar o arquivo bin na flash (_substitua o nome do arquivo pelo que você baixou! Ou use a tecla `TAB` no seu terminal para que ele preencha automaticamente o nome_):
	- WIN (_substitua a porta COM com a sua_): `esptool --port com16 --baud 460800 write-flash --flash_size=detect 0 ESP32_GENERIC_C3-20260824-v1.29.0.bin`
	- Linux (_substitua a porta /dev com a sua_): `esptool --port /dev/ttyUSB0 --baud 460800 write-flash --flash_size=detect 0 ESP32_GENERIC_C3-20260824-v1.29.0.bin`
## Usando um editor para microPython
### Thony, o mais fácil
![Thony Editor Interface](/images/thony.png)

O editor para python Thony é Open Source e bem simples de instalar e usar. Baixe em https://thonny.org/ e instale no seu sistema. Para configurar seu microcontrolador para usar, vá em `Executar > Configurar interpretador` e defina que está usando um **MicroPython (ESP32)** na porta **detectar automaticamente**. Se tudo correr bem, você conseguirá conversar em python com o ESP32 C3 no seu robo openDecabot pelo terminal no canto inferior da tela. Experimente digitar algum comando simples, como 2 + 2, e veja o resultado. Quem estará respondendo será seu robô, e não seu computador.
### ESPIDE, o mais promissor
![ESP IDE interface](/images/espide.png)

O projeto ESP IDE também é Open Source e roda diretamente no navegador. Nele é possível conectar-se à sua microcontroladora diretamente, e usar códigos python ou editor de blocos. Acesse em https://www.espide.eu/en/ .
## Arquivos .py disponíveis
As pastas aqui disponíveis já trazem um conjunto de arquivos .py que funcionam como drivers para diferentes componentes de um openDecabot. Você pode usá-los com qualquer editor, mas iremos experimentar no ESP IDE. Basta usar o editor para copiar esses aquivos para sua microcontroladora.

### Comunicação com um servidor externo
O arquivo `esp32c3/server_client.py` conecta o robô a uma rede Wi-Fi e o faz conversar com o servidor da pasta `/server`: envia telemetria em JSON e executa os comandos devolvidos (`f100`, `d60,450`, `n880,100`...). Edite as configurações no início do arquivo e rode-o, ou adicione `import server_client` ao `main.py`. Protocolo e instruções em [server/README.md](../server/README.md).
