cd /mnt/c/Users/kostd/validator_dashboard

git status
git add .
git commit -m "validator dashboard v0.7"
git tag v0.7

git remote remove origin 2>/dev/null
git remote add origin git@github.com:proxierdima/validator_dashboard.git

git branch -M main
git push -u origin main
git push origin v0.7
