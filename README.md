# ha-logger-ext
Custom Home Assistant component that extends the default logging capabilities by introducing a database-oriented logging layer optimized for Machine Learning workloads.

The component captures events and state changes using a structured schema designed for efficient storage, querying, and downstream data processing. It supports multiple database backends (e.g., MySQL, SQLite) while enforcing a format tailored for analytics and model training pipelines.

Built with a focus on compatibility with Home Assistant standards, the project aims to evolve into an officially adoptable integration without requiring HACS.
