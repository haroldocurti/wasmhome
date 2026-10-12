import pathlib,subprocess,os
root=pathlib.Path('/home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962')
assert os.getuid()==1000 and root.resolve()==root
project=root/'project'
env={'PATH':str(root/'idf526-py313/bin')+':/usr/bin:/bin','HOME':'/home/wasmhome','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1','GIT_TERMINAL_PROMPT':'0'}
subprocess.run([str(root/'idf526-py313/bin/python'),'-B',str(project/'tools/runtime_package.py'),'--acquire'],cwd=project,env=env,check=True)
subprocess.run([str(root/'idf526-py313/bin/python'),'-B',str(project/'tools/runtime_package.py')],cwd=project,env=env,check=True)
