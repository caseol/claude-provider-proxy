# Resiliência do /trello-sync com modelos instáveis

**Data de criação:** 2026-07-07
**Escopo:** proposta de correções no fluxo `/trello-sync` (skill + subagente `trello-bug-fixer` + wrapper `trello.sh`) para tolerar execuções em provedores/modelos instáveis, a partir dos problemas observados na sessão "Revisar" (#1, #21, #23) que rodou em `kimi-k2.7-code` e `nemotron-3-ultra-free`.
**Contexto:** ver [monitoramento de logs de produção](2026-07-06-monitoramento-logs-producao.md) e a skill em `.claude/skills/trello-sync/SKILL.md`.

---

## 1. Problemas observados

A sessão chegou à conclusão correta (os 3 cards já estavam corrigidos e mergeados em `main`), mas por caminhos frágeis. Os sintomas, por ordem de gravidade:

| # | Sintoma | Causa provável | Impacto |
|---|---|---|---|
| P1 | **Tool-calls duplicadas/malformadas** — o modelo emitia dezenas de `[tool_use: Bash ...]` como **texto**, não como chamada real; às vezes com JSON quebrado (`"description": "description":`) ou IDs repetidos | Modelos fracos (nemotron/kimi) não respeitam o protocolo de tool-calling do harness; alucinam a sintaxe em vez de invocar a ferramenta | Ruído enorme no transcript, ações não executadas, risco de repetir efeitos colaterais (mover card N vezes) |
| P2 | **Bloqueios do classificador de auto mode** — `"nemotron-3-ultra-free is temporarily unavailable, so auto mode cannot determine the safety of Bash"` e `"Stage 2 classifier error"` | O classificador de segurança do auto mode usa o próprio modelo da sessão; se ele está instável/indisponível, comandos benignos (`trello.sh move`, `comment`) são bloqueados | Passos param no meio; o fluxo fica em estado parcial (card movido para "Em Execução" mas nunca comentado/despachado) |
| P3 | **Branch órfã deixada para trás** — `trello/revisar-1-21-23` idêntica a `main`, sem commits | Tentativa interrompida no Passo 4; ao recriar com sufixo `-b`, a branch vazia original não foi limpa | Poluição do repositório; confusão em sessões futuras |
| P4 | **Subagente bloqueou por branch errada** — `trello-bug-fixer` abortou porque `git branch --show-current` era `main` | O checkout da branch da sessão falhou silenciosamente (`fatal: a branch ... already exists`) e o orquestrador não detectou antes de despachar | Card marcado como `blocked` sem motivo real |
| P5 | **Verificação incompleta de fixes já mergeados** — os cards #1/#21 foram dados como "resolvidos" só por `git merge-base --is-ancestor`, sem conferir se o fix sobreviveu a reescritas concorrentes do mesmo arquivo | Atalho de verificação; `CpeRemotePage.tsx` (card #1) havia sido reescrito depois do fix original | Risco de reportar "OK" um comportamento que uma mudança posterior quebrou |

---

## 2. Princípio norteador

**O `/trello-sync` não deve depender da inteligência do modelo para ser seguro — deve depender de invariantes verificáveis por script.** Modelo fraco pode ser lento ou verboso, mas não pode deixar o board ou o git em estado inconsistente. A defesa vai em três camadas: (a) o wrapper `trello.sh` fica idempotente; (b) a skill ganha checkpoints de estado explícitos; (c) o fluffo detecta e recomenda a troca de modelo quando a instabilidade é estrutural.

---

## 3. Soluções propostas

### 3.1 P1 — Tool-calls malformadas: guarda no wrapper (idempotência)

O modelo pode alucinar e mandar `trello.sh move <card> <lista>` várias vezes. Torne cada operação **idempotente e verificável**:

- **`move`**: antes de mover, checar se o card já está na lista-alvo (`GET /cards/:id?fields=idList`); se já estiver, no-op com mensagem `já em <lista>`. Evita o "moveu 3x" e torna seguro re-executar.
- **`comment`**: aceitar uma flag opcional `--dedupe-key <slug>` que, antes de postar, busca os últimos N comentários e pula se já existe um com o mesmo marcador (ex.: `🤖 Claude Code iniciou`). Evita floodar o card com comentários repetidos quando o passo é reexecutado.
- **Retorno estruturado**: `trello.sh` passa a imprimir sempre uma linha final `RESULT: ok|noop|error <detalhe>` — assim o orquestrador (mesmo um modelo fraco) tem um sinal textual fácil de parsear, em vez de depender de inferir sucesso do corpo JSON.

### 3.2 P2 — Classificador indisponível: allowlist do `trello.sh`

A raiz do P2 é o auto mode ter que classificar cada `bash .claude/trello/trello.sh ...`. Como esse wrapper é o **único** ponto de contato com o Trello e não faz nada destrutivo fora da API do board, ele deve ser pré-aprovado:

- Adicionar em `.claude/settings.json` uma regra de permissão de Bash cobrindo o prefixo do wrapper, por exemplo:
  ```json
  {
    "permissions": {
      "allow": [
        "Bash(bash .claude/trello/trello.sh:*)"
      ]
    }
  }
  ```
  Com o comando na allowlist, o auto mode **não precisa** invocar o classificador (que é o que falha quando o modelo está instável). Isso elimina os bloqueios `temporarily unavailable` / `Stage 2 classifier error` para as operações do fluxo.
- Usar a skill `fewer-permission-prompts` para gerar/expandir essa allowlist a partir do histórico real de chamadas.
- **Não** allowlistar `git push`, `git checkout -b`, `git branch -D` — essas continuam passando pelo classificador de propósito (são as ações com efeito colateral real).

### 3.3 P3 + P4 — Estado do git: checkpoint atômico no Passo 4

Reescrever o Passo 4 da skill para ser **idempotente e à prova de branch preexistente**, e detectar a falha de checkout antes de qualquer despacho:

```bash
# Passo 4 — criar/entrar na branch da sessão (idempotente)
git checkout main
BRANCH="trello/<lista-slug>-<cards>"
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  # já existe: se está em cima de main sem commits próprios, reusa; senão, sufixa
  if [ -z "$(git log --oneline main..$BRANCH)" ]; then
    git checkout "$BRANCH"        # branch vazia — reaproveita
  else
    BRANCH="$BRANCH-$(date +%H%M)"; git checkout -b "$BRANCH"
  fi
else
  git checkout -b "$BRANCH"
fi
# GUARDA: confirme que o checkout funcionou antes de seguir
[ "$(git branch --show-current)" = "$BRANCH" ] || { echo "ERRO: checkout falhou"; exit 1; }
```

- Adicionar ao Passo 5 uma **pré-condição explícita**: antes de despachar o `trello-bug-fixer`, o orquestrador re-executa `git branch --show-current` e **aborta o card** (não o despacha) se não estiver na branch da sessão — em vez de deixar o subagente descobrir e retornar `blocked`.
- Adicionar ao Passo 6 uma **limpeza de branch órfã garantida**: se a sessão criou uma branch com sufixo porque a original existia vazia, remover a original vazia (`git branch -d` só apaga se mergeada/vazia, então é seguro).

### 3.4 P5 — Cards já mergeados: verificação de sobrevivência

Quando a triagem detecta que um card **já tem fix em `main`** (via commit com `(Trello #N)`), não basta `merge-base --is-ancestor`. A skill deve, antes de mover para "Pronto para Testes":

1. Ler o diff do commit do fix (`git show <sha> --stat`) e identificar os arquivos tocados.
2. Verificar se esses arquivos **foram alterados depois** por outro commit (`git log <sha>..main -- <arquivos>`); se sim, **re-rodar os specs afetados** para confirmar que o comportamento sobreviveu.
3. Só então mover o card, com um comentário que cite o SHA **e** o resultado da reverificação.

Isso captura exatamente o caso do card #1, cujo fix em `CpeRemotePage.tsx` (18/06) precedeu uma reescrita completa do arquivo — que por sorte preservou o gating, mas ninguém tinha confirmado.

### 3.5 Transversal — Detecção de modelo instável e "modo cauteloso"

Adicionar ao Pré-flight da skill uma heurística de auto-diagnóstico:

- Se **2+ chamadas consecutivas** ao `trello.sh` (ou ao classificador) falharem com `temporarily unavailable` / `Stage 2 classifier error`, **parar** e recomendar ao usuário: *"o modelo atual (`<nome>`) está instável para orquestração com tool-calls; troque para um modelo estável (ex.: Sonnet/Opus via `/model`) antes de continuar — o /trello-sync depende de tool-calling confiável."*
- Documentar na própria skill a recomendação: **`/trello-sync` deve rodar em modelo com tool-calling nativo confiável** (Claude Sonnet/Opus/Fable). Modelos free/experimentais servem para tarefas de leitura, não para orquestração que muta board + git.

---

## 4. Prioridade de implementação

| Prioridade | Item | Esforço | Ganho |
|---|---|---|---|
| **Alta** | 3.2 (allowlist do `trello.sh`) | Baixo (1 regra em settings.json) | Elimina a causa mais comum de parada (P2) |
| **Alta** | 3.3 (checkpoint atômico de branch) | Médio (reescrever Passo 4/5/6 da skill) | Elimina branch órfã (P3) e falso-`blocked` (P4) |
| **Média** | 3.1 (idempotência do wrapper) | Médio (editar `trello.sh`) | Torna reexecução segura sob P1 |
| **Média** | 3.5 (detecção de modelo instável) | Baixo (texto na skill + heurística) | Prevenção: falha cedo e claro |
| **Baixa** | 3.4 (verificação de sobrevivência de fix) | Baixo (passos extras na triagem) | Fecha lacuna de verificação (P5) |

---

## 5. Nota sobre a causa raiz

Nenhum desses problemas é bug do código do RouterSafe — todos vêm de rodar um fluxo agêntico (que **muta** estado externo: board Trello + git) em modelos sem tool-calling confiável. A defesa mais barata e eficaz é a **3.2 + 3.5**: pré-aprovar o wrapper (para o classificador instável sair do caminho) e falhar cedo com uma recomendação clara de troca de modelo. As demais (3.1/3.3/3.4) são endurecimento que vale a pena independentemente do modelo, porque tornam o `/trello-sync` seguro para reexecução — útil inclusive no modo `/loop`.
