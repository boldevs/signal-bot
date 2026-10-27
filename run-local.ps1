# Loads .env and runs the bot (crypto + gold/forex every 5 minutes, /stats answered at once). Stop with Ctrl+C.
Set-Location $PSScriptRoot

Get-Content .env | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $name, $value = $line.Split("=", 2)
        Set-Item -Path "Env:$($name.Trim())" -Value $value.Trim().Trim('"')
    }
}

# loop = crypto + gold/forex every 5 minutes, answers /stats and buttons within seconds
python -m bot loop
