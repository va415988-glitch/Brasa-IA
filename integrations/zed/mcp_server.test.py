import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent

class McpProtocolTests(unittest.TestCase):
    def test_initialize_and_tools_list(self):
        process = subprocess.Popen([sys.executable, str(ROOT / 'mcp_server.py')], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env={**os.environ, 'IA_LOCAL_RUNTIME':'http://127.0.0.1:9'})
        process.stdin.write(json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{}})+'\n')
        process.stdin.write(json.dumps({'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}})+'\n')
        process.stdin.close()
        first=json.loads(process.stdout.readline())
        second=json.loads(process.stdout.readline())
        process.wait(timeout=3)
        self.assertEqual(first['result']['serverInfo']['name'],'ia-local-do-zero')
        names={item['name'] for item in second['result']['tools']}
        self.assertIn('edit_file',names)
        self.assertIn('project_checks',names)

if __name__ == '__main__': unittest.main()
