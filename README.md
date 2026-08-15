# Estratégia Curso Downloader

Baixa vídeos, PDFs, slides, mapas mentais e outros materiais dos cursos aos quais a sua conta do Estratégia Concursos tem acesso, organizando o conteúdo por curso e aula.

> Este projeto é um **fork aprimorado** de [suygetsu-bot/estrategia-video-downloader](https://github.com/suygetsu-bot/estrategia-video-downloader). Ele preserva a ideia original e acrescenta uma experiência guiada, PDFs, escolha automática da melhor qualidade, downloads imediatos, retomada e organização automática.

## O que esta versão faz

- abre janelas para solicitar login, ID/URL do curso e pasta de destino;
- não salva sua senha em arquivo — ela permanece somente na memória enquanto o programa roda;
- abre o Microsoft Edge para login, captcha ou autenticação em duas etapas;
- procura livros eletrônicos originais, simplificados e com marcações, PDFs, slides, mapas mentais e outros materiais na página geral e nas aulas;
- baixa cada vídeo na **maior resolução disponibilizada pelo site**;
- começa a baixar assim que encontra cada arquivo, sem esperar a varredura terminar;
- mostra progresso individual e cumulativo, velocidade e estimativas de tempo;
- evita links duplicados e mantém downloads incompletos com a extensão `.part`;
- cria uma subpasta exclusiva com o ID do curso e o timestamp da execução;
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

Para baixar absolutamente todo o conteúdo encontrado, dê duplo clique em:

```text
iniciar.bat
```

Para baixar somente todos os PDFs e slides, use:

```text
iniciar_pdfs_e_slides.bat
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

Ao escolher, por exemplo, `D:\Meus estudos`, o programa cria uma subpasta no
formato `CURSO_ESTRATEGIA_<ID>_<TIMESTAMP_UNIX>`:

```text
D:\Meus estudos\CURSO_ESTRATEGIA_393267_1723680000\
├── Aula 01 - PDF 01 - Livro digital.pdf
├── Aula 01 - PDF 02 - Livro versão simplificada.pdf
├── Aula 01 - PDF 03 - Livro marcação dos aprovados.pdf
├── Aula 01 - Slides 04 - Apresentação da aula.pptx
├── Aula 01 - Mapa Mental 05 - Resumo visual.pdf
├── Aula 01 - Vídeo 01 - Apresentação.mp4
├── Aula 01 - Vídeo 02 - Conteúdo.mp4
├── links_estrategia_conteudo.txt
└── algum-download-interrompido.mp4.part
```

O número final é o timestamp Unix do momento em que a pasta foi criada. Assim,
cada execução fica isolada em sua própria subpasta, mesmo quando você escolhe a
mesma pasta-base. Durante uma execução, arquivos completos já existentes são
ignorados e arquivos `.part` podem ser baixados novamente com segurança.

## Progresso, velocidade e ETA

Durante cada download, uma linha é atualizada aproximadamente a cada segundo com três blocos:

```text
Item #3 42.0% 420 MB/1 GB 12 MB/s ETA 00:48 |
Conhecido 2/3 66.7% 1.2/1.8 GB média 9 MB/s ETA 01:05 |
Curso aula 2/20 ETA~ 03:45:00
```

- **Item** é o arquivo atual: porcentagem, bytes, velocidade e ETA individual.
- **Conhecido** é o acumulado de tudo que já foi localizado: arquivos concluídos, bytes, velocidade média efetiva e ETA desse conjunto.
- **Curso ETA~** é uma aproximação baseada no tamanho médio das aulas já concluídas e na velocidade observada.

O programa baixa cada arquivo assim que o encontra. Por isso, arquivos de aulas ainda não visitadas não entram imediatamente no total: o indicador **Conhecido** cresce durante a varredura. Isso preserva o início imediato dos downloads sem fingir que o tamanho futuro já é conhecido. O ETA do curso aparece como `calculando` no começo e pode ficar indisponível quando o servidor não informa tamanhos ou quando ocorre uma falha.

## Uso pelo terminal (opcional)

Instale as dependências:

```powershell
py -m pip install -r requirements.txt
```

Baixe tudo:

```powershell
py .\estrategia_download_edge_any.py
```

Baixe somente PDFs e slides:

```powershell
py .\estrategia_download_edge_any.py --pdfs-e-slides
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

### Alguma versão do livro, slide ou mapa mental não foi encontrada

O detector reconhece cartões por URL, extensão e textos como “Livro Eletrônico”, “versão simplificada”, “marcação dos aprovados”, “slides”, “apresentação” e “mapa mental”. Quando um cartão reconhecido não expõe uma URL acessível, o programa registra `Material reconhecido, mas sem URL acessível` no log. Nesse caso, abra uma issue com essa linha e uma captura da tela, sem expor credenciais.

### Um arquivo falhou

O programa tenta cada download três vezes e apresenta um resumo final. Se você
executá-lo novamente, uma nova subpasta com outro timestamp será criada para não
misturar execuções diferentes.

### O ETA mudou durante a execução

Isso é esperado. Novos arquivos entram no total conforme as aulas são abertas, e a velocidade da conexão pode variar. O símbolo `~` identifica a estimativa estatística do curso inteiro; o ETA individual tende a ser mais preciso.

## Uso responsável

Use esta ferramenta somente para conteúdos aos quais você tem acesso autorizado e respeite os termos da plataforma e os direitos autorais. Este projeto não é afiliado nem endossado pelo Estratégia Concursos.

## Créditos

- Projeto original: [suygetsu-bot/estrategia-video-downloader](https://github.com/suygetsu-bot/estrategia-video-downloader)
- Este fork: melhorias de usabilidade, autenticação guiada, parametrização do curso, descoberta abrangente de materiais, melhor qualidade disponível, progresso e organização de arquivos.
