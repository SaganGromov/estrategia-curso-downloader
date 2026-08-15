# Estratégia Curso Downloader

Baixa os vídeos e PDFs dos cursos aos quais a sua conta do Estratégia Concursos tem acesso, organizando o conteúdo por curso e aula.

> Este projeto é um **fork aprimorado** de [suygetsu-bot/estrategia-video-downloader](https://github.com/suygetsu-bot/estrategia-video-downloader). Ele preserva a ideia original e acrescenta uma experiência guiada, PDFs, escolha automática da melhor qualidade, downloads imediatos, retomada e organização automática.

## O que esta versão faz

- abre janelas para solicitar login, ID/URL do curso e pasta de destino;
- não salva sua senha em arquivo — ela permanece somente na memória enquanto o programa roda;
- abre o Microsoft Edge para login, captcha ou autenticação em duas etapas;
- procura PDFs na página geral do curso e dentro de cada aula;
- baixa cada vídeo na **maior resolução disponibilizada pelo site**;
- começa a baixar assim que encontra cada arquivo, sem esperar a varredura terminar;
- mostra progresso, tamanho, tentativas, arquivos existentes e falhas;
- evita links duplicados e mantém downloads incompletos com a extensão `.part`;
- cria uma subpasta com o nome do curso;
- registra os links encontrados em `links_estrategia_conteudo.txt`;
- usa o Selenium Manager para cuidar do EdgeDriver automaticamente.

## Uso simples no Windows

Você precisa apenas de:

- Windows 10 ou 11;
- [Python 3](https://www.python.org/downloads/windows/) instalado com a opção **Add Python to PATH** marcada;
- Microsoft Edge instalado;
- uma conta do Estratégia com acesso legítimo ao curso;
- conexão com a internet.

Não é necessário abrir o PowerShell como administrador, configurar variáveis de ambiente ou baixar o `msedgedriver.exe` manualmente.

### 1. Baixe o projeto

Na página deste repositório, clique em **Code → Download ZIP** e extraia o ZIP para uma pasta comum, como Documentos.

### 2. Inicie com duplo clique

Para baixar vídeos e PDFs, dê duplo clique em:

```text
iniciar.bat
```

Para baixar somente os PDFs, use:

```text
iniciar_somente_pdfs.bat
```

Na primeira execução, o iniciador instala automaticamente as duas dependências Python necessárias.

### 3. Responda às janelas

O programa solicitará, nesta ordem:

1. e-mail e senha da conta;
2. ID do curso ou URL completa do curso;
3. pasta-base onde o conteúdo será salvo.

Quando o Edge abrir, clique em **Entrar** e conclua captcha ou autenticação em duas etapas, se aparecer. O programa detecta o painel automaticamente; não é preciso voltar ao terminal e apertar Enter.

## Como encontrar o ID do curso

Abra o curso no navegador. Em uma URL como:

```text
https://www.estrategiaconcursos.com.br/app/dashboard/cursos/393267/aulas
```

o ID é `393267`. Você pode informar apenas o número ou colar a URL inteira na janela.

## Organização dos arquivos

Ao escolher, por exemplo, `D:\Meus estudos`, o programa cria a pasta do curso:

```text
D:\Meus estudos\Nome do curso\
├── Aula 01 - PDF 01 - Livro digital.pdf
├── Aula 01 - Vídeo 01 - Apresentação.mp4
├── Aula 01 - Vídeo 02 - Conteúdo.mp4
├── links_estrategia_conteudo.txt
└── algum-download-interrompido.mp4.part
```

Em uma nova execução na mesma pasta, arquivos completos já existentes são ignorados. Arquivos `.part` podem ser baixados novamente com segurança.

## Uso pelo terminal (opcional)

Instale as dependências:

```powershell
py -m pip install -r requirements.txt
```

Baixe tudo:

```powershell
py .\estrategia_download_edge_any.py
```

Baixe somente PDFs:

```powershell
py .\estrategia_download_edge_any.py --somente-pdfs
```

Consulte as opções:

```powershell
py .\estrategia_download_edge_any.py --help
```

## Configuração avançada

As janelas são o comportamento padrão. Se desejar automatizar parte do preenchimento, estas variáveis continuam disponíveis:

| Variável | Uso |
|---|---|
| `ESTRATEGIA_EMAIL` | Evita a janela de e-mail |
| `ESTRATEGIA_PASSWORD` | Evita a janela de senha |
| `ESTRATEGIA_CURSO_ID` | Preenche inicialmente a janela do curso |
| `DOWNLOAD_DIR` | Define a pasta inicial do seletor |
| `ESTRATEGIA_LOGIN_TIMEOUT` | Tempo máximo de login em segundos; padrão: 600 |
| `ESTRATEGIA_EDGE_DRIVER` | Caminho de um EdgeDriver manual, se você não quiser usar o Selenium Manager |

Exemplo temporário no PowerShell:

```powershell
$env:ESTRATEGIA_EMAIL = "voce@exemplo.com"
$env:ESTRATEGIA_PASSWORD = "sua-senha"
py .\estrategia_download_edge_any.py
```

Evite gravar sua senha em scripts, no README ou em arquivos versionados.

## Solução de problemas

### O Edge não abre

Atualize o Microsoft Edge e confirme que há acesso à internet. O Selenium Manager precisa obter um driver compatível na primeira execução. Usuários avançados podem definir `ESTRATEGIA_EDGE_DRIVER` com um executável próprio.

### O login fica aguardando

Veja se o Edge está esperando clique em **Entrar**, captcha ou autenticação em duas etapas. O limite padrão é de dez minutos.

### Nenhuma aula ou PDF foi encontrado

Confirme o ID informado e o acesso da conta. O site pode alterar sua interface; nesse caso, abra uma issue com a mensagem exibida e, sem expor credenciais, uma descrição da tela.

### Um arquivo falhou

O programa tenta cada download três vezes e apresenta um resumo final. Execute novamente usando a mesma pasta: arquivos completos serão ignorados e os restantes serão tentados de novo.

## Uso responsável

Use esta ferramenta somente para conteúdos aos quais você tem acesso autorizado e respeite os termos da plataforma e os direitos autorais. Este projeto não é afiliado nem endossado pelo Estratégia Concursos.

## Créditos

- Projeto original: [suygetsu-bot/estrategia-video-downloader](https://github.com/suygetsu-bot/estrategia-video-downloader)
- Este fork: melhorias de usabilidade, autenticação guiada, parametrização do curso, PDFs, melhor qualidade disponível, progresso e organização de arquivos.
