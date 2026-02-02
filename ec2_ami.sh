aws ec2 describe-images \
  --region us-east-2 \
  --owners 099720109477 \
  --filters "Name=name,Values=*ubuntu*noble-24.04*server*" \
            "Name=state,Values=available" \
  --query 'Images[*].{Name:Name,ID:ImageId,Date:CreationDate}' \
  --output json | \
jq -r '
  sort_by(.Date) | reverse |
  map(select(.Name | test("eks|pro|minimal") | not)) |
  .[:5] |                      # top 5 most recent
  [.[] | [.ID, .Name, .Date]][] | @tsv'
