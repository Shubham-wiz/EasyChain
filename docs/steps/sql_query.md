# Database query

**Runs a SQL query on a database, or describes its tables for an agent.** An Action.
LangChain-stack term: a SQL tool (SQLAlchemy underneath).

## Settings

| Setting | What it does |
|---|---|
| Database | A SQLAlchemy URL: `postgresql://user:{secret:DB_PASSWORD}@host/db`, `mysql+pymysql://…`, `sqlite:///path/to/file.db`. `{home}` is the Easy Chain data folder (the sample shop database is `sqlite:///{home}/samples/shop.db`). |
| Do | **Run a query**, or **Describe the tables** (their columns and a few rows, so an agent can write its own SQL). |
| SQL | Put Flow Data in with `{field}`: values are sent as parameters, never pasted into the SQL, so they can't change it. A lone `{field}` runs the SQL that field holds (useful for an agent's tool). |
| Read only | On by default. Only `SELECT`-style statements are allowed, and on SQLite, Postgres and MySQL they run in a read-only transaction, so nothing can be changed by mistake, even by an agent. PRAGMAs that change settings are refused. Other databases get a strict check of the SQL only: connect as a user that can only read. With read-only off, every change is committed, including `INSERT … RETURNING`. On Postgres a query may run for 60 seconds. |
| Most rows *(More options)* | Stops a query from returning too much (default 100). |
| Save as | The rows, as a list of `{column: value}`; or the description of the tables. |

## Example

```yaml
- id: order_status
  type: sql_query
  settings:
    connection: postgresql://shop:{secret:SHOP_DB_PASSWORD}@db.internal/shop
    query: SELECT status, total FROM orders WHERE id = {order_id}
    save_as: order
```

The **SQL analyst** template gives an Agent two of these as tools: one that describes the
tables and one that runs the SQL the agent writes, read-only, with a call limit.

## Errors you might see

| Message | What to do |
|---|---|
| Which database? Add its URL | Fill in **Database**. |
| The database password is written into the flow | Put it in a secret (`{secret:DB_PASSWORD}`) and add it under Settings. |
| This query is read-only, so it can only read data | Turn off **Read only** to change data, and consider asking a person to approve each call. |
| An agent can change data with this query | The check suggests keeping it read-only or adding an approval. |
| Run one SQL statement at a time | Split it into several steps. |
