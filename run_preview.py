"""Development preview; never use this token on a public server."""
import os
os.environ.setdefault('ADMIN_TOKEN','local-preview-admin-only-20261017')
os.environ.setdefault('PORT','8766')
import server
server.main()
