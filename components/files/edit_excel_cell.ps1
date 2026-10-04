param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Sheet,
    [Parameter(Mandatory = $true)][int]$Row,
    [Parameter(Mandatory = $true)][int]$Column,
    [Parameter(Mandatory = $true)][string]$Value
)

$ErrorActionPreference = 'Stop'
$excel = $null
$workbook = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $workbook = $excel.Workbooks.Open($Source, 0, $false)
    $cell = $workbook.Worksheets.Item($Sheet).Cells.Item($Row, $Column)
    if ($Value.StartsWith('=')) {
        $cell.Formula = $Value
    }
    else {
        [double]$number = 0
        if ([double]::TryParse($Value, [ref]$number)) { $cell.Value2 = $number }
        else { $cell.Value2 = $Value }
    }
    $workbook.Save()
}
finally {
    if ($workbook) { $workbook.Close($true) }
    if ($excel) { $excel.Quit() }
}
