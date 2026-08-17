# Controle de Gastos Financeiros

Sistema simples de controle de gastos, com conta de usuário e banco de dados local (SQLite — um único arquivo, sem precisar instalar nada além do Python).

## Como rodar

1. Tenha Python 3 instalado no computador.
2. Abra um terminal na pasta do projeto e instale as dependências:

   ```
   pip install -r requirements.txt
   ```

3. Rode a aplicação:

   ```
   python app.py
   ```

4. Abra o navegador em **http://localhost:5000**

O banco de dados (`financas.db`) é criado automaticamente na primeira execução, na mesma pasta do projeto. Todos os seus dados ficam salvos ali — pode fechar e abrir o programa quantas vezes quiser que nada se perde.

## Como usar

1. **Criar conta**: na primeira vez, clique em "Criar conta". Você pode opcionalmente informar seu salário padrão, para não precisar digitá-lo todo mês.
2. **Painel do mês**: escolha o mês/ano, veja receitas, despesas e o quanto sobrou (guardado). Se cadastrou um salário padrão, há um botão para lançá-lo com um clique.
3. **Adicionar lançamento**: escolha a categoria (receitas: Salário, Outros Ganhos; despesas: Comida, Despesas Fixas, Transporte, Entretenimento, Saúde, Férias, Outros), o valor e uma descrição opcional.
4. **Resumo dos Meses**: mostra todos os meses já lançados, com receita, despesa e valor guardado de cada um, além do total guardado somando todos os meses.
5. **Configurações**: atualizar o salário padrão a qualquer momento.

## Observações

- Feito para uso pessoal (uma conta por pessoa, dados isolados por conta).
- Para parar o servidor, use Ctrl+C no terminal.
- Se quiser acessar de outro dispositivo na mesma rede, use o IP da máquina em vez de `localhost` (a aplicação já escuta em todas as interfaces).
