# Loads .env and runs the bot (price check every 10 minutes, /stats answered at once). Stop with Ctrl+C.
Set-Location $PSScriptRoot

Get-Content .env | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $name, $value = $line.Split("=", 2)
        Set-Item -Path "Env:$($name.Trim())" -Value $value.Trim().Trim('"')
    }
}

# watch = checks every 10 minutes and answers /stats within seconds
python -m bot watch
