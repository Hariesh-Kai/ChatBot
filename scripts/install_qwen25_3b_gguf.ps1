$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$venvPython = Join-Path $repoRoot "venv\Scripts\python.exe"

if (Test-Path $venvPython) {
  & $venvPython (Join-Path $PSScriptRoot "install_qwen25_3b_gguf.py")
  exit $LASTEXITCODE
}

python (Join-Path $PSScriptRoot "install_qwen25_3b_gguf.py")
exit $LASTEXITCODE
