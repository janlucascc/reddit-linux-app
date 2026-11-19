# Guia de Publicação no Flathub — Reddit Linux

Este guia detalha o processo completo para submeter e publicar o **Reddit Linux** (`io.github.janlucascc.reddit-linux-app`) no [Flathub](https://flathub.org).

---

## 📁 Estrutura de Arquivos Prontos

Todos os arquivos exigidos pelo padrão Flatpak/Flathub já foram criados e testados com o linter oficial da equipe do Flathub (`flatpak-builder-lint`):

1. **Manifesto do Aplicativo**: [`packaging/flatpak/io.github.janlucascc.reddit-linux-app.yml`](file:///home/janlucas/.gemini/antigravity/scratch/reddit-linux-app/packaging/flatpak/io.github.janlucascc.reddit-linux-app.yml)
   * Base: `io.qt.PySide.BaseApp` (versão `6.11` / Qt 6 com PySide6 e WebEngine pré-configurados)
   * Runtime: `org.kde.Platform` (versão `6.11`)
   * Otimizações: `BASEAPP_DISABLE_NUMPY=1` e `/app/cleanup-BaseApp.sh`
2. **Dependências Python**: [`packaging/flatpak/python3-dependencies.yml`](file:///home/janlucas/.gemini/antigravity/scratch/reddit-linux-app/packaging/flatpak/python3-dependencies.yml)
   * Pacotes `requests` e `cryptography` gerados com hashes SHA256 e wheels pré-compilados para arquiteturas `x86_64` e `aarch64` (sem necessidade de compilar Rust no sandbox).
3. **Metadados AppStream**: [`packaging/flatpak/io.github.janlucascc.reddit-linux-app.metainfo.xml`](file:///home/janlucas/.gemini/antigravity/scratch/reddit-linux-app/packaging/flatpak/io.github.janlucascc.reddit-linux-app.metainfo.xml)
   * Descrição completa, tags OARS 1.1 para classificação etária, URLs de bugs, repositório e screenshots em alta resolução.
   * Validação com `appstreamcli validate --strict` e `flatpak-builder-lint appstream` aprovada (0 erros).
4. **Lançador Desktop**: [`packaging/flatpak/io.github.janlucascc.reddit-linux-app.desktop`](file:///home/janlucas/.gemini/antigravity/scratch/reddit-linux-app/packaging/flatpak/io.github.janlucascc.reddit-linux-app.desktop)
5. **Ícone Vetorial**: [`packaging/flatpak/io.github.janlucascc.reddit-linux-app.svg`](file:///home/janlucas/.gemini/antigravity/scratch/reddit-linux-app/packaging/flatpak/io.github.janlucascc.reddit-linux-app.svg)
6. **Script de Inicialização**: [`packaging/flatpak/reddit-linux`](file:///home/janlucas/.gemini/antigravity/scratch/reddit-linux-app/packaging/flatpak/reddit-linux)

---

## 🚀 Passo a Passo para Enviar ao Flathub

### 1. Fazer o Fork do repositório Flathub
Acesse `https://github.com/flathub/flathub` e clique no botão **Fork** (no topo à direita) para clonar na sua conta `janlucascc`.

### 2. Criar uma nova branch e adicionar os arquivos
No seu computador ou direto pelo GitHub web:

```bash
git clone https://github.com/janlucascc/flathub.git
cd flathub
git checkout -b add-reddit-linux-app

# Copie os 2 arquivos principais para a raiz do repositório flathub:
cp /caminho/do/projeto/packaging/flatpak/io.github.janlucascc.reddit-linux-app.yml .
cp /caminho/do/projeto/packaging/flatpak/python3-dependencies.yml .

git add io.github.janlucascc.reddit-linux-app.yml python3-dependencies.yml
git commit -m "Add io.github.janlucascc.reddit-linux-app"
git push origin add-reddit-linux-app
```

### 3. Abrir o Pull Request
1. Acesse: `https://github.com/flathub/flathub/compare/new-pr...janlucascc:flathub:add-reddit-linux-app`
2. **Atenção à Branch de Destino**: O destino do PR deve ser a branch **`new-pr`** do repositório `flathub/flathub`.
3. Preencha o template do Flathub (marque os checkboxes confirmando que você leu as diretrizes).

### 4. Revisão Automática e Aprovação
* O bot do Flathub (`flathubbot`) iniciará a compilação de teste em x86_64 e aarch64 automaticamente.
* Ele roda o linter oficial (o mesmo que já validamos com sucesso localmente).
* Um revisor voluntário do Flathub aprova o PR.
* Assim que o PR for aceito (*merged*), o Flathub criará automaticamente um repositório próprio para você em:
  `https://github.com/flathub/io.github.janlucascc.reddit-linux-app`
  e enviará um convite de administrador para o seu usuário `janlucascc`.

---

## 🧪 Testar a Compilação Localmente (Opcional)

Se desejar testar a compilação Flatpak no seu próprio computador antes de abrir o PR:

```bash
flatpak run org.flatpak.Builder --user --install --force-clean build-dir packaging/flatpak/io.github.janlucascc.reddit-linux-app.yml
flatpak run io.github.janlucascc.reddit-linux-app
```
