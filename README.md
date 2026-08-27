# Estratégia Curso Downloader

Aplicativo para baixar, com uma interface gráfica local, os conteúdos de cursos aos quais sua conta do Estratégia Concursos possui acesso.

Este projeto é um fork aprimorado de [`suygetsu-bot/estrategia-video-downloader`](https://github.com/suygetsu-bot/estrategia-video-downloader). O fork acrescenta painel gráfico, coleta de materiais além dos vídeos, instalação automática dos componentes internos, progresso detalhado, retomada de transferências e diversas proteções de confiabilidade.

> Use somente com conteúdos aos quais você possui acesso legítimo e respeite os termos da plataforma e a legislação aplicável. O projeto não contorna login, captcha, 2FA ou permissões da conta.

## Como usar no Windows

Você precisa apenas de:

- Windows 10 ou Windows 11;
- Microsoft Edge instalado;
- conexão com a internet;
- permissão normal para gravar em sua própria pasta de usuário.

Você **não precisa** instalar Python, pip, Selenium, WebDriver, Git ou ferramentas de programação. Não é necessário executar nada como Administrador nem alterar o `PATH`.

### 1. Baixe e extraia o aplicativo

Na página do GitHub, use **Code → Download ZIP**. Depois clique com o botão direito no ZIP, escolha **Extrair tudo** e abra a pasta extraída.

Não execute o aplicativo diretamente de dentro da visualização do ZIP. Caminhos com espaços, acentos e parênteses são aceitos.

### 2. Dê duplo clique em `iniciar.bat`

Na primeira execução, uma janela mostrará o preparo automático:

```text
Estratégia Curso Downloader

[1/5] Verificando os componentes necessários...
[2/5] Preparando o Python interno...
[3/5] Preparando o ambiente do aplicativo...
[4/5] Instalando componentes necessários...
[5/5] Iniciando o Estratégia Curso Downloader...
```

O aplicativo prepara uma cópia privada do Python quando necessária e um ambiente isolado em:

```text
%LOCALAPPDATA%\EstrategiaCursoDownloader\
```

Isso não modifica o `PATH`, outras instalações de Python nem configurações globais do Windows. A instalação é feita no perfil do usuário, sem elevação. As próximas inicializações reutilizam o ambiente e normalmente são bem mais rápidas.

### 3. Use o painel

No painel aberto no Edge:

1. informe o e-mail e a senha da conta;
2. para baixar um curso, informe o ID, como `393267`, ou cole a URL completa;
   no modo de todos os cursos esse campo não é necessário;
3. aceite a pasta padrão em `Downloads\Estrategia` ou use **Alterar pasta…**;
4. escolha o modo;
5. clique em **Abrir login e iniciar**.

Uma janela controlada do Edge será aberta com os campos preenchidos. Clique em **Entrar** e conclua captcha ou verificação em duas etapas, se solicitado. O painel detecta o login e prossegue automaticamente. Não volte ao terminal nem pressione Enter nele.

## Modos de download

**Conteúdo completo**, que é o padrão:

- vídeos na maior qualidade anunciada pelo site;
- PDFs e livros eletrônicos, incluindo versões original, simplificada e “marcação dos aprovados”;
- slides;
- mapas mentais;
- outros anexos reconhecidos.

**Materiais de estudo**:

- PDFs;
- slides;
- mapas mentais;
- sem vídeos.

**Modo bombado — todos os cursos**:

- consulta o catálogo completo da conta depois da autenticação;
- percorre todos os cursos acessíveis e baixa o mesmo conteúdo completo de cada
  um;
- isola a falha de um curso e continua nos seguintes;
- em uma coleção já criada, reaudita todos os cursos e baixa somente arquivos
  ausentes, incompletos ou cujo tamanho não coincide com o servidor.

Escolha a pasta-base antes de iniciar. Na primeira execução o aplicativo cria
uma coleção identificável; nas execuções seguintes basta escolher a mesma pasta
ou a própria coleção. O arquivo de controle não contém cookies, credenciais nem
URLs de download.

O atalho `iniciar_pdfs_e_slides.bat` abre o mesmo painel com o segundo modo já selecionado.

## Durante o download

Os materiais são baixados assim que cada resposta de aula é validada; o
programa não espera terminar o curso inteiro. O painel mostra:

- fase atual e aula atual;
- item atual, porcentagem, velocidade e ETA;
- progresso dos arquivos já encontrados;
- estimativa aproximada do curso, enquanto ainda há aulas desconhecidas;
- arquivos encontrados, baixados, existentes e com falha;
- atividade em tempo real;
- espaço livre no destino.

Depois do login, a descoberta não lê nem recarrega o DOM. O programa faz um
`GET /api/aluno/curso/<ID>` para obter a contagem e os IDs únicos das aulas e,
para cada aula disponível, exatamente um `GET /api/aluno/aula/<ID-DA-AULA>`.
Cada resposta enumera os vídeos e suas resoluções, livros eletrônicos original,
simplificado e grifado, resumos, slides, mapas mentais, áudios, thumbnails e
outros campos conhecidos. A maior resolução numérica de cada vídeo é escolhida.

A resposta do curso também fornece `is_disponivel` e `data_publicacao` por ID de
aula. Detalhes ainda não liberados não são consultados repetidamente: o curso
fica em `aguardando_liberacao`, com as datas persistidas, depois de todo o
conteúdo atualmente acessível ser validado. Uma nova execução reavalia o mesmo
ID quando a data chegar.

A contagem declarada deve coincidir exatamente com a lista de aulas e não pode
haver IDs duplicados. Todo vídeo precisa ter uma URL de resolução utilizável.
Se a API introduzir uma URL em um campo ainda não classificado, ou se qualquer
transferência falhar, o programa preserva os arquivos concluídos e recusa o
estado `completo`. Assim, ausência numa resposta canônica pode ser aceita;
ausência momentânea no React não é mais usada como prova.

O botão **Cancelar download** pede confirmação, interrompe a transferência cooperativamente, fecha o Edge controlado e preserva arquivos completos e `.part` válidos.

Ao concluir, o painel apresenta um resumo e os botões **Abrir pasta**, **Ver detalhes**, **Copiar diagnóstico** e **Encerrar interface**.

## Pastas de saída

Uma execução nova de curso individual consulta o título canônico na API e cria
uma pasta descritiva em `kebab-case`, sem espaços nem acentos. O ID e o timestamp
continuam no final para tornar cada execução rastreável:

```text
<titulo-do-curso>-id-<ID>-<UNIX_TIMESTAMP>
```

Exemplo:

```text
bacen-analista-area-2-economia-e-financas-macroeconomia-parte-do-conhecimentos-especificos-id-327532-1723680000
├── aula_00
│   ├── videos
│   └── pdfs
├── aula_01
│   ├── videos
│   └── pdfs
├── aula_02
│   ├── videos
│   └── pdfs
└── links_estrategia_conteudo.txt
```

No modo bombado, a pasta da coleção se chama
`estrategia-cursos-completos`. Cada curso usa um nome igualmente descritivo, mas
determinístico e sem timestamp:

```text
estrategia-cursos-completos/
└── bacen-analista-area-2-economia-e-financas-macroeconomia-parte-do-conhecimentos-especificos-id-327532/
    ├── aula_00/
    ├── aula_01/
    ├── links_estrategia_conteudo.txt
    ├── .inventario_estrategia.json
    └── .estado_estrategia.json
```

O marcador `.estrategia_colecao.json` relaciona o ID do painel ao título exato,
à pasta e ao estado `em_andamento`, `incompleto`, `completo` ou
`aguardando_liberacao`. Por isso uma nova execução reconhece a coleção mesmo que
ela tenha sido interrompida.

Quando uma ou mais aulas ainda não possuem conteúdo acessível, mas a API marca
`is_disponivel: false` e fornece datas futuras em `data_publicacao`, o curso
recebe `aguardando_liberacao` e a próxima data fica no manifesto. Isso não é
contado como erro nem como conclusão definitiva: uma execução posterior
reutiliza a mesma pasta, preserva o conteúdo já validado e verifica somente o
que tiver sido liberado.

O arquivo `.inventario_estrategia.json` registra a versão da auditoria e os
recursos enumerados pela API por aula. As identidades são hashes SHA-256; URLs
temporárias, parâmetros assinados, cookies e tokens nunca são persistidos.
O arquivo é atualizado atomicamente depois de cada aula e seus checkpoints
compatíveis são preservados em uma retomada, sempre com estado `em_andamento`
ou `incompleto` até que o curso inteiro volte a passar pela auditoria.
Somente um inventário com `versao_auditoria: 4`, a contagem exata de aulas da
API, um snapshot de API para cada ID único de aula e uma ocorrência física
validada em cada pasta sustenta o estado `completo`. Não há recargas repetidas
da página para tentar provar estabilidade. Se uma resposta trouxer uma URL em
um campo ainda desconhecido, a execução registra uma pendência e recusa o estado
`completo`, em vez de ignorar silenciosamente o possível recurso. Quando duas
aulas apontam para o mesmo objeto remoto, ele é baixado uma vez e materializado
também na segunda aula por vínculo físico (ou cópia local quando o sistema de
arquivos não permite vínculos). Marcadores produzidos por versões anteriores
devem ser considerados pendentes até uma nova auditoria.

Cada aula recebe suas próprias pastas `videos` e `pdfs`, inclusive a
`aula_00`. PDFs, slides e mapas mentais ficam em `pdfs`. Anexos que não sejam
vídeos nem documentos PDF ficam em `outros_materiais`, criada somente quando
necessária. Assim, os arquivos de uma aula não ficam misturados com os das
demais.

Cada pasta contém também `.estado_estrategia.json`. Se uma execução falhar,
for cancelada ou for interrompida, informar novamente o mesmo curso reutiliza
automaticamente a pasta incompleta mais recente. Arquivos completos são
ignorados e os ausentes são procurados novamente. Pastas antigas sem marcador
também são retomadas uma vez, o que permite completar downloads feitos por
versões anteriores; depois de uma auditoria concluída sem falhas, uma nova
execução volta a criar outra pasta descritiva e timestampada.

Falhas transitórias retomam o `.part` com HTTP `Range` quando o servidor
permite. Se o servidor ignorar a faixa, o arquivo é reiniciado com segurança;
uma resposta inconsistente nunca é anexada cegamente.

### Certificação offline, sem nova varredura do curso

Depois que o inventário v4 chega a `completo`, o resultado pode ser certificado
sem reabrir o Edge, chamar a API ou repetir downloads:

```bash
python3 tools/verify_course_download.py \
  /mnt/f/estrategia-cursos-completos/bacen-lingua-portuguesa-id-327527
```

O verificador percorre a árvore local uma vez, reconstrói o nome esperado de
cada ocorrência do manifesto, rejeita lacunas mesmo na presença de arquivos
legados extras, rejeita `.part` e outros transitórios, chama `pdfinfo`, `ffprobe`
ou `identify` para formatos conhecidos e calcula SHA-256. Hard links do mesmo
conteúdo são lidos uma única vez. Somente uma verificação aprovada grava
`.certificado_integridade_estrategia.json`; o certificado contém caminhos
relativos, tamanhos e hashes, nunca URLs, cookies ou tokens.

`pdfinfo`, `ffprobe` e `identify` são validadores opcionais externos e estão
disponíveis no WSL usado no desenvolvimento. Se algum deles não estiver no
`PATH`, a verificação profunda falha explicitamente. `--no-structure` dispensa
esses executáveis e ainda permite um certificado baseado em manifesto,
tamanho e SHA-256, com `estrutura_verificada: false` registrado no resultado.

Para uma conferência rápida do mapeamento manifesto-arquivos, sem calcular
hashes nem criar certificado:

```powershell
py .\tools\verify_course_download.py `
  "F:\estrategia-cursos-completos\bacen-lingua-portuguesa-id-327527" `
  --no-hash --no-structure
```

Essa certificação verifica o snapshot já registrado. Uma execução futura
ainda consulta uma vez a API para detectar conteúdo novo que a plataforma tenha
publicado depois do snapshot, mas reutiliza tudo que o manifesto local já
confirmou.

## Instalação automática e segurança

O bootstrap usa uma versão de Python explicitamente fixada em `bootstrap-config.json` e dependências testadas em `requirements.lock.txt`.

Antes de executar um instalador do Python, ele valida:

- a origem oficial `python.org`;
- o SHA-256 esperado;
- a assinatura Authenticode da Python Software Foundation.

Os logs técnicos ficam em:

```text
%LOCALAPPDATA%\EstrategiaCursoDownloader\logs\
```

Se o runtime, o ambiente virtual ou uma instalação de pacotes estiver incompleta, o bootstrap tenta reparar ou recriar apenas os componentes privados do aplicativo. Mudanças no arquivo de dependências são detectadas por hash.

## Privacidade

- a senha é mantida somente em memória durante a autenticação e depois apagada do estado da aplicação;
- senha, cookies, cabeçalhos de autorização e token da interface não entram no diagnóstico;
- parâmetros sensíveis de URLs são removidos de logs e do arquivo informativo de links, mas preservados internamente na requisição real;
- a interface atende somente em `127.0.0.1`, exige uma sessão aleatória e usa cabeçalhos restritivos;
- o token inicial é trocado por um cookie local `HttpOnly` e removido da barra de endereço.

## Mensagens inesperadas do site

O site às vezes abre um alerta nativo de um assistente virtual não configurado. O aplicativo reconhece esse alerta irrelevante, fecha-o, registra um aviso curto e retoma a operação segura sem reiniciar o curso ou perder arquivos já concluídos.

Alertas desconhecidos não são descartados silenciosamente. O texto é preservado de forma sanitizada; se a mensagem voltar a impedir a operação, o download para com uma explicação compreensível e mantém os arquivos existentes.

## Solução de problemas

### “Alguns arquivos do aplicativo não foram encontrados”

Extraia o ZIP inteiro e execute `iniciar.bat` na pasta extraída. Não copie somente o `.bat`.

### Microsoft Edge não foi encontrado

O Edge é o único navegador suportado nesta versão. Instale-o pelo site oficial da Microsoft e tente novamente. O Selenium Manager obtém automaticamente o Edge WebDriver compatível; não baixe `msedgedriver.exe` manualmente.

### Não foi possível baixar Python ou componentes

Confira a conexão e tente novamente. Proxy corporativo, firewall, AppLocker, antivírus ou políticas da empresa podem bloquear downloads ou execução. A mensagem mostra o local do `bootstrap.log`, que contém os detalhes técnicos.

### O curso não tem aulas ou materiais

Confira o ID/URL, confirme que o login terminou e verifique se a conta realmente
possui acesso ao curso. Uma resposta válida da API pode confirmar que uma aula
disponível está vazia. Uma aula marcada como indisponível sem data futura, uma
contagem divergente ou um detalhe recusado inesperadamente mantém o curso como
`incompleto`.

### A execução terminou com conteúdo pendente

Abra **Ver detalhes** ou copie o diagnóstico para identificar os itens marcados
com 🚩. Inicie novamente o mesmo curso e escolha a mesma pasta-base; o programa
retomará a pasta incompleta, validará novamente as listas e tentará apenas o que
ainda não estiver completo.

### Disco sem espaço

Escolha outro destino ou libere espaço. Arquivos completos não são apagados automaticamente.

## Uso avançado opcional

O fluxo normal não exige terminal. Para quem já usa Python, os pontos de entrada históricos continuam disponíveis:

```powershell
py .\estrategia_download_edge_any.py
py .\estrategia_download_edge_any.py --pdfs-e-slides
```

Variáveis de ambiente preservadas:

```text
ESTRATEGIA_EMAIL
ESTRATEGIA_PASSWORD
ESTRATEGIA_CURSO_ID
DOWNLOAD_DIR
ESTRATEGIA_LOGIN_TIMEOUT
ESTRATEGIA_EDGE_DRIVER
ESTRATEGIA_DEBUG
```

`ESTRATEGIA_EDGE_DRIVER` é apenas uma compatibilidade avançada. Usuários comuns devem deixar o Selenium Manager cuidar do driver.

### Consultar o nome exato de um curso

O utilitário independente abaixo recebe o mesmo ID numérico usado na URL da
área do aluno:

```powershell
py .\course_name.py 327532
```

Depois do login normal no Edge, o `stdout` contém somente o título canônico
devolvido pela API. Avisos de login e erros usam `stderr`. O Edge é usado apenas
para estabelecer a sessão legítima; a consulta do nome não lê DOM, HTML nem
`page_source`.

O código reutilizável está em `estrategia_downloader/course_metadata.py`:

```python
from estrategia_downloader.course_metadata import (
    create_course_api_session,
    get_course_name,
)
from estrategia_downloader.downloads import criar_sessao_download

web_session = criar_sessao_download(driver, course_url)
api_session = create_course_api_session(web_session)
name = get_course_name(api_session, "327532")
```

`create_course_api_session()` usa os cookies copiados do Edge somente para
pedir ao site a credencial temporária usada pela própria SPA. Depois disso, a
sessão de API contém somente o cabeçalho `Authorization`, e a consulta é um
`GET` direto. O endpoint não é público nem documentado e pode mudar; nenhuma
credencial é gravada ou exibida. A versão 4.0 usa esse mesmo mecanismo para o
título e para o inventário completo do curso.

Para validar somente o inventário, sem baixar arquivos nem inspecionar o DOM:

```powershell
py .\tools\check_course_api_inventory.py 327530 327535
```

O utilitário aceita vários IDs na mesma sessão, imprime apenas títulos, IDs,
contagens e caminhos JSON seguros e fecha a janela controlada do Edge ao final.

### Executar ou dividir o catálogo pelo terminal

A interface é o caminho recomendado. Para auditorias avançadas, o utilitário
abaixo executa o mesmo orquestrador e aceita volumes adicionais. Um curso já
registrado permanece no seu volume; um curso novo vai para o volume com mais
espaço livre:

```powershell
py .\tools\download_full_catalog.py `
  --destination "F:\estrategia-cursos-completos" `
  --spillover "E:\estrategia-cursos-completos-e" `
  --spillover "G:\estrategia-cursos-completos-g"
```

`--include-regex` e `--exclude-regex` permitem auditar grupos por ID ou título.
`--submit-login` pode acionar o botão de login quando as variáveis de ambiente
já foram fornecidas; captcha e 2FA continuam sendo resolvidos normalmente no
Edge e nunca são contornados.

Para repetir a descoberta e a matriz de minimização de autenticação sem abrir
manualmente dezenas de requisições no DevTools:

```powershell
py .\tools\probe_course_api.py 327532
```

O probe habilita o log de performance apenas em sua própria janela controlada,
classifica respostas Fetch/XHR em memória, reproduz candidatos seguros com
`requests` e remove todos os valores de query string de seu relatório.

## Arquitetura

```text
iniciar.bat
└── bootstrap.ps1
    ├── Python/runtime gerenciado
    ├── ambiente isolado e dependências fixadas
    └── estrategia_download_edge_any.py
        └── estrategia_downloader/
            ├── app.py          autenticação, download e orquestração
            ├── alerts.py       recuperação de alertas do Edge
            ├── browser.py      criação confiável do Edge
            ├── collection.py   coleção integral, nomes e estado retomável
            ├── course_inventory.py inventário canônico de aulas e recursos
            ├── course_metadata.py consulta direta do nome canônico do curso
            ├── discovery.py    classificação e parsing testável
            ├── downloads.py    HTTP, retomada, disco e progresso
            ├── diagnostics.py  relatório sanitizado
            ├── errors.py       mensagens amigáveis
            └── utils.py        nomes, URLs e utilitários
```

`interface_web.py` mantém o servidor local e o estado; `interface/` contém HTML, CSS e JavaScript sem frameworks externos.

## Testes

Os testes não usam credenciais reais e não acessam cursos reais. Eles cobrem:

- bootstrap, metadados, launchers e caminhos Windows complexos;
- API local, autenticação, modos, cancelamento e estados finais;
- contrato da API de cursos e aulas, contagens exatas, IDs únicos, resoluções de
  vídeo, variantes de material e campos de URL desconhecidos;
- garantia de uma chamada por aula disponível e nenhuma chamada de detalhe para
  aulas cuja própria API marca como futuras;
- fixtures HTML sanitizados mantidos para os diagnósticos legados;
- retomada automática de pastas incompletas e migração segura de pastas legadas;
- catálogo integral, reauditoria, isolamento de falhas, filtros e divisão segura
  de cursos inteiros entre volumes;
- nomes descritivos de curso em `kebab-case`, com ID e timestamp rastreáveis;
- nomes reservados e caracteres especiais do Windows;
- URLs sensíveis e duplicatas;
- transferências novas e retomadas HTTP válidas/inválidas;
- alerta do assistente virtual, inclusive a transição da Aula 11 para a Aula 12.

O CI principal roda no Windows com a mesma combinação fixada de Python e dependências. Há workflows separados para o bootstrap e para uma integração periódica com Edge real.

## Limitações conhecidas

- somente Microsoft Edge é suportado atualmente;
- mudanças futuras nas APIs não documentadas podem exigir atualização dos campos
  reconhecidos; URLs desconhecidas bloqueiam a conclusão para evitar omissões;
- redes ou políticas corporativas podem impedir downloads ou execução, mesmo sem necessidade de Administrador;
- a suíte automatizada não autentica em uma conta real e não baixa um curso real;
- URLs temporárias podem expirar no servidor e exigir uma nova autenticação.
